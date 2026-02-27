# Ncf-PtWait - Phase 1 Clinic Cycle-Time App

Internal FastAPI + Jinja2 + SQLite app for patient cycle-time studies.

## Features
- Session-based login with internal users (`admin`, `fd1`, `nurse1` on first run).
- Required queue context selectors (Location + Provider).
- Front Desk workflow: create visit, set **Arrived**, set **Ready for Clinical**.
- Nurse workflow: set **Intake Complete**, **Provider In**, **Provider Out**, optional **Lab Complete**, and **Checkout**.
- Timestamp locking: once set, normal users cannot change.
- Admin override route with required reason and full audit logging.
- Delay note inline editing for nurse/admin.
- Excel export with raw timestamps and calculated duration metrics.

## Run locally
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Seed users
Created automatically if database is empty:
- `admin / ChangeMeAdmin!` (admin)
- `fd1 / ChangeMeFD!` (fd)
- `nurse1 / ChangeMeNurse!` (nurse)

On initial seed, app logs a warning to change passwords.

## Notes
- All times are treated as America/New_York local time and stored as naive SQLite datetimes.
- Database file defaults to `clinic_cycle_time.db` in project root.
