# Ncf-PtWait - Patient Cycle Time

Internal FastAPI + Jinja2 + SQLite application for patient cycle-time tracking and operational auditability.

## Current Scope
- Role-based workflows for Front Desk, Nurse, Auditor, and Admin.
- Queue tracking from Arrived through Checkout with optional Other/Lab/X-Ray/Ultrasound steps.
- Admin timestamp override with required reason and audit trail.
- Visit export and combined audit-log export to Excel.
- Lightweight visual effects (user-toggleable) designed to stay low overhead.

## Security and Session Controls
- Session cookie uses `SameSite=Lax` and a 12-hour max age.
- Idle session timeout is enforced at 1 hour.
- Absolute session timeout is enforced at 12 hours.
- Unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`) are same-origin checked.
- Lightweight in-memory rate limits protect login and sensitive admin endpoints.
- Security headers are applied globally (`X-Frame-Options`, `nosniff`, CSP baseline, etc.).
- Unhandled exceptions are logged server-side and return a generic 500 response.

## Roles and Access
- `Front Desk`: create visits, Arrived, Ready for Clinical.
- `Nurse`: intake/provider/other/lab/checkout workflow actions.
- `Auditor`: full timestamp workflow access (same action depth as Admin), but no admin-only parameter or override tools.
- `Admin`: full workflow plus admin pages, override, parameters, logs export, purge, backup.

## Auditability
- Visit-level timestamp edits are written to `AuditLog`.
- Administrative/system actions are written to `AdminActionLog`.
- Admin actions currently logged include:
- timestamp override
- purge all-zero MRN records
- audit exports
- user creation/role/password/hidden updates
- location/provider add/edit/hidden updates
- daily goal updates
- database backup action

## Validation and Guardrails
- MRN is numeric-only.
- Server-side length limits are enforced for key inputs (username, password, visit type, notes, location/provider names, search).
- Export date range is validated and capped to prevent oversized requests.
- Temporary-password users are forced through password change before normal app access.

## Exports
- Visit export includes cycle-time timestamps and calculated operational durations.
- Combined audit export includes both visit audit rows and system audit rows in a dedicated workbook.

## Backup and Recovery
- Admin page includes `Create DB Backup`.
- Backups are written to `backups/` under the project root with timestamped filenames.
- Primary SQLite DB file is `clinic_cycle_time.db` in project root.

## Health Endpoint
- `GET /healthz` returns a simple JSON status for service liveness checks.

## Deployment Model (HTTPS via Proxy)
- This app is intended to run behind your existing HTTPS proxy/termination layer.
- Keep app process on internal HTTP (`uvicorn ... --host 127.0.0.1 --port 8000` or internal LAN bind as needed).
- Do not force HTTPS in app code unless your proxy/session forwarding model is changed and validated.
- Do not publish internal/private IPs in shared documentation.

## Run Locally (Windows)
1. Create and activate venv:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
pip install bcrypt==3.2.2 --force-reinstall
```

3. Start app:

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Server Run Command (Scan Server)
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Windows Service (NSSM)
1. Install `NSSM`.
2. Create service:

```powershell
nssm install Ncf-PtWait
```

3. Use:
- Application path: `C:\Codex\Ncf-PtWait\.venv\Scripts\python.exe`
- Startup directory: `C:\Codex\Ncf-PtWait`
- Arguments: `-m uvicorn app.main:app --host 127.0.0.1 --port 8000`

4. Start and set auto-start:

```powershell
nssm start Ncf-PtWait
sc.exe config Ncf-PtWait start= auto
```

5. Check status:

```powershell
sc.exe query Ncf-PtWait
```

## Install / Host Shortcuts
- DNS Manager: `dnsmgmt.msc`
- Certificate Local Machine: `certlm.msc`
- Certificate Templates: `certtmpl.msc`
- Certificate Services: `certsrv.msc`
- MMC: `mmc`
- IIS Manager / Port Forwarding: `inetmgr`

## Seed Users
Seed users are created automatically on empty DB:
- `admin / ChangeMeAdmin!`
- `fd1 / ChangeMeFD!`
- `nurse1 / ChangeMeNurse!`

## Time and Storage Notes
- App logic uses America/New_York local time semantics.
- SQLite timestamps are stored as naive datetimes in the DB.
- Database path is resolved from project root to avoid accidental duplicate DB creation from alternate working directories.
