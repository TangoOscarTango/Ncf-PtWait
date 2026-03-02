from __future__ import annotations

from datetime import date, datetime, time
from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import AuditLog, RoleEnum, User, Visit

TIME_FIELDS = [
    "arrived_at",
    "ready_for_clinical_at",
    "intake_complete_at",
    "provider_in_at",
    "provider_out_at",
    "lab_complete_at",
    "checkout_at",
]

FIELD_LABELS = {
    "arrived_at": "Arrived",
    "ready_for_clinical_at": "Ready for Clinical",
    "intake_complete_at": "Intake Complete",
    "provider_in_at": "Provider In",
    "provider_out_at": "Provider Out",
    "lab_complete_at": "Lab Complete",
    "checkout_at": "Checkout",
}

DELAY_NOTE_FIELDS = {
    "arrived_at": "arrived_delay_note",
    "ready_for_clinical_at": "ready_for_clinical_delay_note",
    "intake_complete_at": "intake_complete_delay_note",
    "provider_in_at": "provider_in_delay_note",
    "provider_out_at": "provider_out_delay_note",
    "lab_complete_at": "lab_complete_delay_note",
    "checkout_at": "checkout_delay_note",
}

ROLE_ACTIONS = {
    RoleEnum.FD: {"arrived_at", "ready_for_clinical_at"},
    RoleEnum.NURSE: {"intake_complete_at", "provider_in_at", "provider_out_at", "lab_complete_at", "checkout_at"},
    RoleEnum.ADMIN: set(TIME_FIELDS),
}


class ValidationError(Exception):
    pass


def now_local() -> datetime:
    return datetime.now()


def can_set_field(visit: Visit, field_name: str) -> bool:
    if getattr(visit, field_name) is not None:
        return False

    if field_name == "arrived_at":
        return True
    if field_name == "ready_for_clinical_at":
        return visit.arrived_at is not None
    if field_name == "intake_complete_at":
        return visit.ready_for_clinical_at is not None
    if field_name == "provider_in_at":
        return visit.intake_complete_at is not None
    if field_name == "provider_out_at":
        return visit.provider_in_at is not None
    if field_name == "lab_complete_at":
        return visit.intake_complete_at is not None
    if field_name == "checkout_at":
        if visit.provider_out_at is None:
            return False
        return True
    return False


def get_next_field(visit: Visit) -> str | None:
    if visit.arrived_at is None:
        return "arrived_at"
    if visit.ready_for_clinical_at is None:
        return "ready_for_clinical_at"
    if visit.intake_complete_at is None:
        return "intake_complete_at"
    if visit.lab_complete_at is None and visit.provider_in_at is None:
        return "lab_complete_at_or_provider_in"
    if visit.provider_in_at is None:
        return "provider_in_at"
    if visit.lab_complete_at is None and visit.provider_out_at is None:
        return "lab_complete_at_or_provider_out"
    if visit.provider_out_at is None:
        return "provider_out_at"
    if visit.checkout_at is None:
        return "checkout_at"
    return None


def current_status(visit: Visit) -> str:
    next_field = get_next_field(visit)
    if next_field is None:
        return "Complete"
    if next_field == "arrived_at":
        return "Awaiting Arrival"
    if next_field == "lab_complete_at_or_provider_in":
        return "Intake Complete - Optional Lab or Provider In"
    if next_field == "lab_complete_at_or_provider_out":
        return "Provider In - Optional Lab or Provider Out"
    return f"Awaiting {FIELD_LABELS[next_field]}"


def lab_duration_minutes(visit: Visit) -> float | None:
    if not visit.lab_complete_at:
        return None
    if visit.provider_out_at and visit.provider_out_at <= visit.lab_complete_at:
        return minutes_between(visit.provider_out_at, visit.lab_complete_at)
    if visit.provider_in_at and visit.provider_in_at <= visit.lab_complete_at:
        return minutes_between(visit.provider_in_at, visit.lab_complete_at)
    if visit.intake_complete_at:
        return minutes_between(visit.intake_complete_at, visit.lab_complete_at)
    return None


def delay_note_entries(visit: Visit) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for field_name in TIME_FIELDS:
        note_value = getattr(visit, DELAY_NOTE_FIELDS[field_name])
        if note_value:
            entries.append((FIELD_LABELS[field_name], note_value))
    return entries


def set_timestamp(
    visit: Visit,
    field_name: str,
    acting_user: User,
    db: Session,
    delay_note: str | None = None,
) -> None:
    if field_name not in TIME_FIELDS:
        raise ValidationError("Invalid field.")

    if field_name not in ROLE_ACTIONS[acting_user.role]:
        raise ValidationError("You are not allowed to perform this action.")

    now_value = now_local()
    update_values = {
        field_name: now_value,
        DELAY_NOTE_FIELDS[field_name]: delay_note.strip() if delay_note and delay_note.strip() else None,
        "updated_at": now_value,
    }
    conditions = [Visit.id == visit.id, getattr(Visit, field_name).is_(None)]

    if field_name == "ready_for_clinical_at":
        conditions.append(Visit.arrived_at.is_not(None))
    elif field_name == "intake_complete_at":
        conditions.append(Visit.ready_for_clinical_at.is_not(None))
    elif field_name == "provider_in_at":
        conditions.append(Visit.intake_complete_at.is_not(None))
    elif field_name == "provider_out_at":
        conditions.append(Visit.provider_in_at.is_not(None))
    elif field_name == "lab_complete_at":
        conditions.append(Visit.intake_complete_at.is_not(None))
    elif field_name == "checkout_at":
        conditions.append(Visit.provider_out_at.is_not(None))

    result = db.execute(update(Visit).where(*conditions).values(**update_values))
    if result.rowcount != 1:
        db.rollback()
        current_visit = db.query(Visit).filter(Visit.id == visit.id).first()
        if not current_visit:
            raise ValidationError("Visit not found.")
        db.refresh(current_visit)
        for attribute in TIME_FIELDS:
            setattr(visit, attribute, getattr(current_visit, attribute))
        for attribute in DELAY_NOTE_FIELDS.values():
            setattr(visit, attribute, getattr(current_visit, attribute))
        visit.updated_at = current_visit.updated_at
        if getattr(current_visit, field_name) is not None:
            raise ValidationError("This step was already recorded by another user. The queue has been refreshed.")
        raise ValidationError("This visit changed before your save completed. The queue has been refreshed.")

    db.commit()
    db.refresh(visit)


def override_timestamp(
    visit: Visit,
    field_name: str,
    new_value: datetime | None,
    reason: str,
    acting_user: User,
    db: Session,
) -> None:
    if acting_user.role != RoleEnum.ADMIN:
        raise ValidationError("Only admins can override timestamps.")
    if field_name not in TIME_FIELDS:
        raise ValidationError("Invalid timestamp field.")
    if not reason.strip():
        raise ValidationError("Reason is required.")

    old_value = getattr(visit, field_name)
    setattr(visit, field_name, new_value)
    visit.updated_at = now_local()

    audit = AuditLog(
        visit_id=visit.id,
        field_name=field_name,
        old_value=format_dt(old_value),
        new_value=format_dt(new_value),
        changed_by_user_id=acting_user.id,
        changed_at=now_local(),
        reason=reason.strip(),
    )
    db.add(visit)
    db.add(audit)
    db.commit()


def format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def minutes_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    return round((end - start).total_seconds() / 60, 2)


def build_export(
    visits: list[Visit],
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "CycleTime"

    headers = [
        "MRN",
        "Location",
        "Provider",
        "Arrived At",
        "Arrived Delay Reason",
        "Ready for Clinical At",
        "Ready for Clinical Delay Reason",
        "Intake Complete At",
        "Intake Complete Delay Reason",
        "Provider In At",
        "Provider In Delay Reason",
        "Provider Out At",
        "Provider Out Delay Reason",
        "Lab Complete At",
        "Lab Complete Delay Reason",
        "Checkout At",
        "Checkout Delay Reason",
        "Registration Minutes",
        "Waiting Room Minutes",
        "Provider Wait Minutes",
        "Provider Duration Minutes",
        "Lab Duration Minutes",
        "Total Visit Minutes",
    ]
    ws.append(headers)
    ws.freeze_panes = "A2"

    for cell in ws[1]:
        cell.font = Font(bold=True)

    for column_index in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(column_index)].width = 18.0

    for visit in visits:
        ws.append(
            [
                visit.mrn,
                visit.location.name,
                visit.provider.name,
                format_dt(visit.arrived_at),
                visit.arrived_delay_note,
                format_dt(visit.ready_for_clinical_at),
                visit.ready_for_clinical_delay_note,
                format_dt(visit.intake_complete_at),
                visit.intake_complete_delay_note,
                format_dt(visit.provider_in_at),
                visit.provider_in_delay_note,
                format_dt(visit.provider_out_at),
                visit.provider_out_delay_note,
                format_dt(visit.lab_complete_at),
                visit.lab_complete_delay_note,
                format_dt(visit.checkout_at),
                visit.checkout_delay_note,
                minutes_between(visit.arrived_at, visit.ready_for_clinical_at),
                minutes_between(visit.ready_for_clinical_at, visit.intake_complete_at),
                minutes_between(visit.intake_complete_at, visit.provider_in_at),
                minutes_between(visit.provider_in_at, visit.provider_out_at),
                lab_duration_minutes(visit),
                minutes_between(visit.arrived_at, visit.checkout_at),
            ]
        )

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.read()


def day_range(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, time.min), datetime.combine(day, time.max)
