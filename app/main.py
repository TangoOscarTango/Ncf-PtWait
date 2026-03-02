from datetime import date, datetime
from io import BytesIO
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from app.auth import authenticate_user, get_current_user, hash_password, verify_password
from app.db import Base, SessionLocal, engine, get_db
from app.models import AuditLog, Location, Provider, RoleEnum, User, Visit
from app.seed import seed_initial_data
from app.services import (
    DELAY_NOTE_FIELDS,
    FIELD_LABELS,
    TIME_FIELDS,
    ValidationError,
    build_export,
    current_status,
    day_range,
    delay_note_entries,
    format_dt,
    get_next_field,
    lab_duration_minutes,
    minutes_between,
    override_timestamp,
    set_timestamp,
)

app = FastAPI(title="Clinic Cycle Time")
app.add_middleware(SessionMiddleware, secret_key="replace-this-in-production", https_only=False)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 32


def role_label(role: RoleEnum) -> str:
    return {
        RoleEnum.ADMIN: "Admin",
        RoleEnum.FD: "Front Desk",
        RoleEnum.NURSE: "Nurse",
    }[role]


templates.env.globals["role_label"] = role_label


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_user_preference_columns(db)
        ensure_visit_delay_note_columns(db)
        seed_initial_data(db)
    finally:
        db.close()


def set_flash(request: Request, level: str, message: str) -> None:
    request.session["flash"] = {"level": level, "message": message}


def pop_flash(request: Request):
    return request.session.pop("flash", None)


def require_user(request: Request, db: Session) -> User | RedirectResponse:
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    return user


def ensure_user_preference_columns(db: Session) -> None:
    columns = {
        row[1]
        for row in db.execute(text("PRAGMA table_info(users)")).fetchall()
    }
    if "preferred_location_id" not in columns:
        db.execute(text("ALTER TABLE users ADD COLUMN preferred_location_id INTEGER"))
    if "preferred_provider_id" not in columns:
        db.execute(text("ALTER TABLE users ADD COLUMN preferred_provider_id INTEGER"))
    db.commit()


def ensure_visit_delay_note_columns(db: Session) -> None:
    columns = {
        row[1]
        for row in db.execute(text("PRAGMA table_info(visits)")).fetchall()
    }
    for column_name in DELAY_NOTE_FIELDS.values():
        if column_name not in columns:
            db.execute(text(f"ALTER TABLE visits ADD COLUMN {column_name} TEXT"))
    db.commit()


def persist_user_context(user: User, location_id: int | None, provider_id: int | None, db: Session) -> None:
    changed = False
    if location_id is not None and user.preferred_location_id != location_id:
        user.preferred_location_id = location_id
        changed = True
    if provider_id is not None and user.preferred_provider_id != provider_id:
        user.preferred_provider_id = provider_id
        changed = True
    if changed:
        db.add(user)
        db.commit()
        db.refresh(user)


def build_filter_query(
    location_ids: list[int],
    provider_ids: list[int],
    visit_date: str,
    search: str | None,
    hide_complete: bool,
) -> str:
    params: list[tuple[str, str]] = []
    params.extend([("location_ids", str(location_id)) for location_id in location_ids])
    params.extend([("provider_ids", str(provider_id)) for provider_id in provider_ids])
    params.extend(
        [
            ("visit_date", visit_date),
            ("search", search or ""),
            ("hide_complete", str(hide_complete).lower()),
        ]
    )
    return urlencode(params)


def dashboard_redirect_url(
    location_ids: list[int],
    provider_ids: list[int],
    visit_date: str,
    search: str | None,
    hide_complete: bool,
) -> str:
    return "/dashboard?" + build_filter_query(location_ids, provider_ids, visit_date, search, hide_complete)


def parameters_page_context(request: Request, user: User, db: Session) -> dict:
    return {
        "request": request,
        "current_user": user,
        "users": db.query(User).order_by(User.username.asc()).all(),
        "locations": db.query(Location).order_by(Location.name.asc()).all(),
        "providers": db.query(Provider).order_by(Provider.name.asc()).all(),
        "flash": pop_flash(request),
    }


def normalize_selected_ids(values: list[int] | None, valid_ids: set[int]) -> list[int]:
    if not values:
        return []
    return [value for value in dict.fromkeys(values) if value in valid_ids]


def filter_summary(options: list[Location | Provider], selected_ids: list[int], empty_label: str) -> str:
    if not selected_ids:
        return empty_label
    selected_names = [option.name for option in options if option.id in selected_ids]
    if len(selected_names) <= 2:
        return ", ".join(selected_names)
    return f"{len(selected_names)} selected"


def visit_matches_dashboard_filters(visit: Visit, user: User, search_text_lower: str, hide_complete: bool) -> bool:
    if hide_complete:
        if user.role == RoleEnum.FD and visit.ready_for_clinical_at is not None:
            return False
        if user.role in {RoleEnum.NURSE, RoleEnum.ADMIN} and visit.checkout_at is not None:
            return False

    if search_text_lower:
        searchable_text = " ".join(
            [
                visit.mrn or "",
                visit.location.name or "",
                visit.provider.name or "",
                " ".join(note for _, note in delay_note_entries(visit)),
            ]
        ).lower()
        if search_text_lower not in searchable_text:
            return False

    return True


@app.get("/", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    if current_user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, username=username, password=password)
    if not user:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid username or password."}, status_code=401
        )

    request.session["user_id"] = user.id
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    location_ids: list[int] | None = Query(default=None),
    provider_ids: list[int] | None = Query(default=None),
    visit_date: str | None = None,
    search: str | None = None,
    hide_complete: bool = False,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    locations = db.query(Location).order_by(Location.name).all()
    providers = db.query(Provider).order_by(Provider.name).all()
    valid_location_ids = {location.id for location in locations}
    valid_provider_ids = {provider.id for provider in providers}

    selected_location_ids = normalize_selected_ids(location_ids, valid_location_ids)
    selected_provider_ids = normalize_selected_ids(provider_ids, valid_provider_ids)

    if not selected_location_ids and user.preferred_location_id in valid_location_ids:
        selected_location_ids = [user.preferred_location_id]
    if not selected_provider_ids and user.preferred_provider_id in valid_provider_ids:
        selected_provider_ids = [user.preferred_provider_id]

    if selected_location_ids or selected_provider_ids:
        persist_user_context(
            user,
            selected_location_ids[0] if selected_location_ids else None,
            selected_provider_ids[0] if selected_provider_ids else None,
            db,
        )

    today = date.today()
    selected_date = today
    if visit_date:
        try:
            selected_date = datetime.strptime(visit_date, "%Y-%m-%d").date()
        except ValueError:
            selected_date = today

    search_text = (search or "").strip()
    search_text_lower = search_text.lower()

    visits = []
    if selected_location_ids and selected_provider_ids:
        start_dt, end_dt = day_range(selected_date)
        visits = (
            db.query(Visit)
            .options(joinedload(Visit.location), joinedload(Visit.provider))
            .filter(
                Visit.location_id.in_(selected_location_ids),
                Visit.provider_id.in_(selected_provider_ids),
                Visit.created_at >= start_dt,
                Visit.created_at <= end_dt,
            )
            .order_by(Visit.created_at.asc())
            .all()
        )

    prepared_visits = []
    for visit in visits:
        if not visit_matches_dashboard_filters(visit, user, search_text_lower, hide_complete):
            continue

        next_field = get_next_field(visit)
        if next_field == "lab_complete_at_or_provider_out":
            if user.role in {RoleEnum.NURSE, RoleEnum.ADMIN}:
                action_label = "Lab Complete (Optional)"
                action_field = "lab_complete_at"
                alt_action_label = "Provider Out"
                alt_action_field = "provider_out_at"
                action_enabled = True
            else:
                action_label = "Lab Complete (Optional)"
                action_field = "lab_complete_at"
                alt_action_label = None
                alt_action_field = None
                action_enabled = False
        elif next_field:
            action_field = next_field
            action_label = FIELD_LABELS[next_field]
            alt_action_label = None
            alt_action_field = None
            if user.role == RoleEnum.FD:
                action_enabled = next_field in {"arrived_at", "ready_for_clinical_at"}
            elif user.role == RoleEnum.NURSE:
                action_enabled = next_field in {
                    "intake_complete_at",
                    "provider_in_at",
                    "provider_out_at",
                    "lab_complete_at",
                    "checkout_at",
                }
            else:
                action_enabled = True
        else:
            action_field = None
            action_label = "Complete"
            alt_action_label = None
            alt_action_field = None
            action_enabled = False

        prepared_visits.append(
            {
                "visit": visit,
                "status": current_status(visit),
                "next_field": action_field,
                "next_label": action_label,
                "next_enabled": action_enabled,
                "alt_action_label": alt_action_label,
                "alt_action_field": alt_action_field,
                "registration_min": minutes_between(visit.arrived_at, visit.ready_for_clinical_at),
                "waiting_room_min": minutes_between(visit.ready_for_clinical_at, visit.intake_complete_at),
                "provider_wait_min": minutes_between(visit.intake_complete_at, visit.provider_in_at),
                "provider_duration_min": minutes_between(visit.provider_in_at, visit.provider_out_at),
                "lab_duration_min": lab_duration_minutes(visit),
                "total_visit_min": minutes_between(visit.arrived_at, visit.checkout_at),
            }
        )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": user,
            "locations": locations,
            "providers": providers,
            "selected_location_ids": selected_location_ids,
            "selected_provider_ids": selected_provider_ids,
            "selected_location_id": selected_location_ids[0] if selected_location_ids else None,
            "selected_provider_id": selected_provider_ids[0] if selected_provider_ids else None,
            "location_filter_label": filter_summary(locations, selected_location_ids, "Select locations"),
            "provider_filter_label": filter_summary(providers, selected_provider_ids, "Select providers"),
            "clear_filter_query": build_filter_query(
                selected_location_ids,
                selected_provider_ids,
                selected_date.strftime("%Y-%m-%d"),
                None,
                False,
            ),
            "selected_date": selected_date.strftime("%Y-%m-%d"),
            "search": search_text,
            "hide_complete": hide_complete,
            "visits": prepared_visits,
            "flash": pop_flash(request),
            "format_dt": format_dt,
        },
    )


@app.post("/visits")
def create_visit(
    request: Request,
    mrn: str = Form(...),
    location_id: int = Form(...),
    provider_id: int = Form(...),
    location_ids: list[int] = Form(default=[]),
    provider_ids: list[int] = Form(default=[]),
    visit_date: str | None = Form(default=None),
    search: str | None = Form(default=None),
    hide_complete: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role not in {RoleEnum.FD, RoleEnum.ADMIN}:
        set_flash(request, "error", "Only FD/Admin can create visits.")
        return RedirectResponse(url="/dashboard", status_code=303)

    persist_user_context(user, location_id, provider_id, db)

    if not mrn.strip():
        set_flash(request, "error", "MRN is required.")
        return RedirectResponse(
            url=dashboard_redirect_url(
                location_ids or [location_id],
                provider_ids or [provider_id],
                visit_date or date.today().strftime("%Y-%m-%d"),
                search,
                hide_complete,
            ),
            status_code=303,
        )

    visit = Visit(
        mrn=mrn.strip(),
        location_id=location_id,
        provider_id=provider_id,
        created_by_user_id=user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(visit)
    db.commit()
    set_flash(request, "success", f"Visit for MRN {visit.mrn} created.")
    return RedirectResponse(
        url=dashboard_redirect_url(
            location_ids or [location_id],
            provider_ids or [provider_id],
            visit_date or date.today().strftime("%Y-%m-%d"),
            search,
            hide_complete,
        ),
        status_code=303,
    )




@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        "account.html",
        {
            "request": request,
            "current_user": user,
            "flash": pop_flash(request),
        },
    )


@app.post("/account/username")
def account_update_username(
    request: Request,
    username: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    candidate = username.strip()
    if not candidate:
        set_flash(request, "error", "Username is required.")
        return RedirectResponse(url="/account", status_code=303)

    existing_user = db.query(User).filter(User.username == candidate, User.id != user.id).first()
    if existing_user:
        set_flash(request, "error", "That username is already in use.")
        return RedirectResponse(url="/account", status_code=303)

    user.username = candidate
    db.add(user)
    db.commit()
    set_flash(request, "success", "Username updated.")
    return RedirectResponse(url="/account", status_code=303)


@app.post("/account/password")
def account_update_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if not verify_password(current_password, user.password_hash):
        set_flash(request, "error", "Current password is incorrect.")
        return RedirectResponse(url="/account", status_code=303)
    if len(new_password) < MIN_PASSWORD_LENGTH:
        set_flash(request, "error", f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return RedirectResponse(url="/account", status_code=303)
    if len(new_password) > MAX_PASSWORD_LENGTH:
        set_flash(request, "error", f"New password must be {MAX_PASSWORD_LENGTH} characters or fewer.")
        return RedirectResponse(url="/account", status_code=303)
    if new_password != confirm_password:
        set_flash(request, "error", "New password and confirmation do not match.")
        return RedirectResponse(url="/account", status_code=303)

    user.password_hash = hash_password(new_password)
    db.add(user)
    db.commit()
    set_flash(request, "success", "Password updated.")
    return RedirectResponse(url="/account", status_code=303)


@app.post("/visits/{visit_id}/action")
def visit_action(
    request: Request,
    visit_id: int,
    action_field: str = Form(...),
    location_ids: list[int] = Form(...),
    provider_ids: list[int] = Form(...),
    visit_date: str = Form(...),
    delay_note: str | None = Form(default=None),
    search: str | None = Form(default=None),
    hide_complete: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    persist_user_context(
        user,
        location_ids[0] if location_ids else None,
        provider_ids[0] if provider_ids else None,
        db,
    )

    visit = db.query(Visit).filter(Visit.id == visit_id).first()
    if not visit:
        set_flash(request, "error", "Visit not found.")
    else:
        try:
            set_timestamp(visit, action_field, user, db, delay_note=delay_note)
            set_flash(request, "success", f"{FIELD_LABELS[action_field]} recorded for MRN {visit.mrn}.")
        except ValidationError as exc:
            set_flash(request, "error", str(exc))

    return RedirectResponse(
        url=dashboard_redirect_url(location_ids, provider_ids, visit_date, search, hide_complete),
        status_code=303,
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    mrn: str | None = None,
    search_date: str | None = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)

    query = db.query(Visit).options(joinedload(Visit.location), joinedload(Visit.provider)).order_by(Visit.created_at.desc())
    if mrn:
        query = query.filter(Visit.mrn == mrn.strip())
    if search_date:
        try:
            d = datetime.strptime(search_date, "%Y-%m-%d").date()
            start_dt, end_dt = day_range(d)
            query = query.filter(Visit.created_at >= start_dt, Visit.created_at <= end_dt)
        except ValueError:
            pass

    visits = query.limit(100).all()
    selected_visit_id = request.query_params.get("visit_id")
    audit_rows = []
    if selected_visit_id:
        audit_rows = (
            db.query(AuditLog)
            .options(joinedload(AuditLog.changed_by_user))
            .filter(AuditLog.visit_id == int(selected_visit_id))
            .order_by(AuditLog.changed_at.desc())
            .all()
        )

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "current_user": user,
            "visits": visits,
            "time_fields": TIME_FIELDS,
            "field_labels": FIELD_LABELS,
            "audit_rows": audit_rows,
            "selected_visit_id": int(selected_visit_id) if selected_visit_id else None,
            "flash": pop_flash(request),
            "mrn": mrn or "",
            "search_date": search_date or "",
            "format_dt": format_dt,
        },
    )


@app.post("/admin/override")
def admin_override(
    request: Request,
    visit_id: int = Form(...),
    field_name: str = Form(...),
    new_value: str = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)

    visit = db.query(Visit).filter(Visit.id == visit_id).first()
    if not visit:
        set_flash(request, "error", "Visit not found.")
        return RedirectResponse(url="/admin", status_code=303)

    parsed_new_value = None
    if new_value.strip():
        try:
            parsed_new_value = datetime.strptime(new_value, "%Y-%m-%dT%H:%M")
        except ValueError:
            set_flash(request, "error", "Invalid datetime format.")
            return RedirectResponse(url=f"/admin?visit_id={visit_id}", status_code=303)

    try:
        override_timestamp(visit, field_name, parsed_new_value, reason, user, db)
        set_flash(request, "success", "Timestamp overridden and audit log updated.")
    except ValidationError as exc:
        set_flash(request, "error", str(exc))

    return RedirectResponse(url=f"/admin?visit_id={visit_id}", status_code=303)


@app.get("/export", response_class=HTMLResponse)
def export_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    locations = db.query(Location).order_by(Location.name).all()
    providers = db.query(Provider).order_by(Provider.name).all()
    return templates.TemplateResponse(
        "export.html",
        {
            "request": request,
            "current_user": user,
            "locations": locations,
            "providers": providers,
            "today": date.today().strftime("%Y-%m-%d"),
        },
    )


@app.post("/export/download")
def export_download(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    location_id: str | None = Form(default=None),
    provider_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return Response("Invalid date range.", status_code=400)

    start_dt, _ = day_range(start)
    _, end_dt = day_range(end)
    try:
        parsed_location_id = int(location_id) if location_id and location_id.strip() else None
        parsed_provider_id = int(provider_id) if provider_id and provider_id.strip() else None
    except ValueError:
        return Response("Invalid location or provider filter.", status_code=400)

    query = (
        db.query(Visit)
        .options(joinedload(Visit.location), joinedload(Visit.provider))
        .filter(Visit.created_at >= start_dt, Visit.created_at <= end_dt)
        .order_by(Visit.created_at.asc())
    )
    if parsed_location_id:
        query = query.filter(Visit.location_id == parsed_location_id)
    if parsed_provider_id:
        query = query.filter(Visit.provider_id == parsed_provider_id)

    try:
        data = build_export(query.all())
    except Exception:
        return Response("Excel export failed.", status_code=500)

    filename = f"clinic_cycle_time_{start_date}_to_{end_date}.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.get("/parameters", response_class=HTMLResponse)
def parameters_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse("parameters.html", parameters_page_context(request, user, db))


@app.post("/parameters/users")
def parameters_create_user(
    request: Request,
    username: str = Form(...),
    role: RoleEnum = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)

    candidate = username.strip()
    if not candidate:
        set_flash(request, "error", "Username is required.")
        return RedirectResponse(url="/parameters", status_code=303)
    if len(password) < MIN_PASSWORD_LENGTH:
        set_flash(request, "error", f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return RedirectResponse(url="/parameters", status_code=303)
    if len(password) > MAX_PASSWORD_LENGTH:
        set_flash(request, "error", f"Password must be {MAX_PASSWORD_LENGTH} characters or fewer.")
        return RedirectResponse(url="/parameters", status_code=303)
    if db.query(User).filter(User.username == candidate).first():
        set_flash(request, "error", "That username already exists.")
        return RedirectResponse(url="/parameters", status_code=303)

    db.add(User(username=candidate, password_hash=hash_password(password), role=role, is_active=True))
    db.commit()
    set_flash(request, "success", f"User {candidate} created.")
    return RedirectResponse(url="/parameters", status_code=303)


@app.post("/parameters/users/{user_id}/reset-password")
def parameters_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)

    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        set_flash(request, "error", "User not found.")
        return RedirectResponse(url="/parameters", status_code=303)
    if len(new_password) < MIN_PASSWORD_LENGTH:
        set_flash(request, "error", f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return RedirectResponse(url="/parameters", status_code=303)
    if len(new_password) > MAX_PASSWORD_LENGTH:
        set_flash(request, "error", f"Password must be {MAX_PASSWORD_LENGTH} characters or fewer.")
        return RedirectResponse(url="/parameters", status_code=303)

    target_user.password_hash = hash_password(new_password)
    db.add(target_user)
    db.commit()
    set_flash(request, "success", f"Password reset for {target_user.username}.")
    return RedirectResponse(url="/parameters", status_code=303)


@app.post("/parameters/locations")
def parameters_add_location(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)

    candidate = name.strip()
    if not candidate:
        set_flash(request, "error", "Location name is required.")
        return RedirectResponse(url="/parameters", status_code=303)
    if db.query(Location).filter(Location.name == candidate).first():
        set_flash(request, "error", "That location already exists.")
        return RedirectResponse(url="/parameters", status_code=303)

    db.add(Location(name=candidate))
    db.commit()
    set_flash(request, "success", f"Location {candidate} added.")
    return RedirectResponse(url="/parameters", status_code=303)


@app.post("/parameters/locations/{location_id}")
def parameters_update_location(
    request: Request,
    location_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)

    location = db.query(Location).filter(Location.id == location_id).first()
    candidate = name.strip()
    if not location:
        set_flash(request, "error", "Location not found.")
        return RedirectResponse(url="/parameters", status_code=303)
    if not candidate:
        set_flash(request, "error", "Location name is required.")
        return RedirectResponse(url="/parameters", status_code=303)
    if db.query(Location).filter(Location.name == candidate, Location.id != location_id).first():
        set_flash(request, "error", "That location name is already in use.")
        return RedirectResponse(url="/parameters", status_code=303)

    location.name = candidate
    db.add(location)
    db.commit()
    set_flash(request, "success", "Location updated.")
    return RedirectResponse(url="/parameters", status_code=303)


@app.post("/parameters/providers")
def parameters_add_provider(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)

    candidate = name.strip()
    if not candidate:
        set_flash(request, "error", "Provider name is required.")
        return RedirectResponse(url="/parameters", status_code=303)
    if db.query(Provider).filter(Provider.name == candidate).first():
        set_flash(request, "error", "That provider already exists.")
        return RedirectResponse(url="/parameters", status_code=303)

    db.add(Provider(name=candidate))
    db.commit()
    set_flash(request, "success", f"Provider {candidate} added.")
    return RedirectResponse(url="/parameters", status_code=303)


@app.post("/parameters/providers/{provider_id}")
def parameters_update_provider(
    request: Request,
    provider_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != RoleEnum.ADMIN:
        return RedirectResponse(url="/dashboard", status_code=303)

    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    candidate = name.strip()
    if not provider:
        set_flash(request, "error", "Provider not found.")
        return RedirectResponse(url="/parameters", status_code=303)
    if not candidate:
        set_flash(request, "error", "Provider name is required.")
        return RedirectResponse(url="/parameters", status_code=303)
    if db.query(Provider).filter(Provider.name == candidate, Provider.id != provider_id).first():
        set_flash(request, "error", "That provider name is already in use.")
        return RedirectResponse(url="/parameters", status_code=303)

    provider.name = candidate
    db.add(provider)
    db.commit()
    set_flash(request, "success", "Provider updated.")
    return RedirectResponse(url="/parameters", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
