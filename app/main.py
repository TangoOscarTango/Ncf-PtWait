from datetime import date, datetime

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from app.auth import authenticate_user, get_current_user
from app.db import Base, SessionLocal, engine, get_db
from app.models import AuditLog, Location, Provider, RoleEnum, User, Visit
from app.seed import seed_initial_data
from app.services import (
    FIELD_LABELS,
    TIME_FIELDS,
    ValidationError,
    build_export,
    current_status,
    day_range,
    format_dt,
    get_next_field,
    minutes_between,
    override_timestamp,
    set_timestamp,
    update_delay_note,
)

app = FastAPI(title="Clinic Cycle Time")
app.add_middleware(SessionMiddleware, secret_key="replace-this-in-production", https_only=False)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
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
    location_id: int | None = None,
    provider_id: int | None = None,
    visit_date: str | None = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    locations = db.query(Location).order_by(Location.name).all()
    providers = db.query(Provider).order_by(Provider.name).all()

    today = date.today()
    selected_date = today
    if visit_date:
        try:
            selected_date = datetime.strptime(visit_date, "%Y-%m-%d").date()
        except ValueError:
            selected_date = today

    visits = []
    if location_id and provider_id:
        start_dt, end_dt = day_range(selected_date)
        visits = (
            db.query(Visit)
            .options(joinedload(Visit.location), joinedload(Visit.provider))
            .filter(
                Visit.location_id == location_id,
                Visit.provider_id == provider_id,
                Visit.created_at >= start_dt,
                Visit.created_at <= end_dt,
            )
            .order_by(Visit.created_at.asc())
            .all()
        )

    prepared_visits = []
    for visit in visits:
        next_field = get_next_field(visit)
        if next_field == "lab_complete_at_or_checkout":
            if user.role in {RoleEnum.NURSE, RoleEnum.ADMIN}:
                action_label = "Lab Complete"
                action_field = "lab_complete_at"
                alt_action_label = "Skip Lab & Checkout"
                alt_action_field = "checkout_at"
                action_enabled = True
            else:
                action_label = "Lab Complete"
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
                "lab_duration_min": minutes_between(visit.provider_out_at, visit.lab_complete_at),
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
            "selected_location_id": location_id,
            "selected_provider_id": provider_id,
            "selected_date": selected_date.strftime("%Y-%m-%d"),
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
    delay_note: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role not in {RoleEnum.FD, RoleEnum.ADMIN}:
        set_flash(request, "error", "Only FD/Admin can create visits.")
        return RedirectResponse(url="/dashboard", status_code=303)

    if not mrn.strip():
        set_flash(request, "error", "MRN is required.")
        return RedirectResponse(url=f"/dashboard?location_id={location_id}&provider_id={provider_id}", status_code=303)

    visit = Visit(
        mrn=mrn.strip(),
        location_id=location_id,
        provider_id=provider_id,
        delay_note=delay_note.strip() if delay_note else None,
        created_by_user_id=user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db.add(visit)
    db.commit()
    set_flash(request, "success", f"Visit for MRN {visit.mrn} created.")
    return RedirectResponse(url=f"/dashboard?location_id={location_id}&provider_id={provider_id}", status_code=303)


@app.post("/visits/{visit_id}/action")
def visit_action(
    request: Request,
    visit_id: int,
    action_field: str = Form(...),
    location_id: int = Form(...),
    provider_id: int = Form(...),
    visit_date: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    visit = db.query(Visit).filter(Visit.id == visit_id).first()
    if not visit:
        set_flash(request, "error", "Visit not found.")
    else:
        try:
            set_timestamp(visit, action_field, user, db)
            set_flash(request, "success", f"{FIELD_LABELS[action_field]} recorded for MRN {visit.mrn}.")
        except ValidationError as exc:
            set_flash(request, "error", str(exc))

    return RedirectResponse(
        url=f"/dashboard?location_id={location_id}&provider_id={provider_id}&visit_date={visit_date}", status_code=303
    )


@app.post("/visits/{visit_id}/delay-note")
def visit_delay_note(
    request: Request,
    visit_id: int,
    delay_note: str | None = Form(default=None),
    location_id: int = Form(...),
    provider_id: int = Form(...),
    visit_date: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user

    visit = db.query(Visit).filter(Visit.id == visit_id).first()
    if not visit:
        set_flash(request, "error", "Visit not found.")
    else:
        try:
            update_delay_note(visit, delay_note, user, db)
            set_flash(request, "success", "Delay note updated.")
        except ValidationError as exc:
            set_flash(request, "error", str(exc))

    return RedirectResponse(
        url=f"/dashboard?location_id={location_id}&provider_id={provider_id}&visit_date={visit_date}", status_code=303
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
    location_id: int | None = Form(default=None),
    provider_id: int | None = Form(default=None),
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

    query = (
        db.query(Visit)
        .options(joinedload(Visit.location), joinedload(Visit.provider))
        .filter(Visit.created_at >= start_dt, Visit.created_at <= end_dt)
        .order_by(Visit.created_at.asc())
    )
    if location_id:
        query = query.filter(Visit.location_id == location_id)
    if provider_id:
        query = query.filter(Visit.provider_id == provider_id)

    data = build_export(query.all())
    filename = f"clinic_cycle_time_{start_date}_to_{end_date}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )
