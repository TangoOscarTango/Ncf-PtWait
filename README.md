# Ncf-PtWait - Phase 1 Clinic Cycle-Time App

Internal FastAPI + Jinja2 + SQLite app for patient cycle-time studies.

## Features
- Session-based login with internal users (`admin`, `fd1`, `nurse1` on first run).
- Required queue context selectors (Location + Provider).
- Front Desk workflow: create visit, set **Arrived**, set **Ready for Clinical**.
- Nurse workflow: set **Intake Complete**, **Provider In**, optional **Lab Complete**, **Provider Out**, and **Checkout**.
- The dashboard remembers each user's last selected location and provider and restores them after login.
- Delay reasons are captured per timestamp action and included in Excel exports.
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

## Production Deployment (Windows Server LAN)
Deploy one server-hosted instance on the Windows server at `10.34.0.11`. All users access it from their browsers at:

`http://10.34.0.11:8000`

### Production Uvicorn command
Run from the project root on the server:

```powershell
cd C:\Codex\Ncf-PtWait
.venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

This runs without `--reload`, which is the correct production setting for LAN deployment.

### Server-only SQLite database
- The application stores SQLite at `C:\Codex\Ncf-PtWait\clinic_cycle_time.db`.
- All database reads and writes happen on the server process only.
- Client browsers never create or store database files.
- The app now resolves the database path from the project root instead of the shell working directory, so starting the process from another folder will not create a second SQLite file elsewhere on the server.

### Allow port 8000 in Windows Firewall
Run PowerShell as Administrator on the server:

```powershell
netsh advfirewall firewall add rule name="Ncf-PtWait 8000" dir=in action=allow protocol=TCP localport=8000
```

To verify the rule:

```powershell
netsh advfirewall firewall show rule name="Ncf-PtWait 8000"
```

### Run as a persistent Windows service
Recommended approach: use `NSSM` to wrap the Uvicorn process as a Windows service.

1. Install `NSSM` on the server.
2. Create the service:

```powershell
nssm install Ncf-PtWait
```

3. In the NSSM dialog, set:
- Application path: `C:\Codex\Ncf-PtWait\.venv\Scripts\python.exe`
- Startup directory: `C:\Codex\Ncf-PtWait`
- Arguments: `-m uvicorn app.main:app --host 0.0.0.0 --port 8000`

4. Save the service, then start it:

```powershell
nssm start Ncf-PtWait
```

5. To make it start automatically with Windows:

```powershell
sc.exe config Ncf-PtWait start= auto
```

6. To check service status:

```powershell
sc.exe query Ncf-PtWait
```

### Notes for LAN use
- Session authentication is cookie-based per browser, so multiple simultaneous users on the LAN can stay logged in independently.
- Templates and navigation do not depend on `localhost` or `127.0.0.1`.
- The default application host/port is `0.0.0.0:8000`.

## Seed users
Created automatically if database is empty:
- `admin / ChangeMeAdmin!` (admin)
- `fd1 / ChangeMeFD!` (fd)
- `nurse1 / ChangeMeNurse!` (nurse)

On initial seed, app logs a warning to change passwords.

## Notes
- All times are treated as America/New_York local time and stored as naive SQLite datetimes.
- Database file defaults to `clinic_cycle_time.db` in project root.
