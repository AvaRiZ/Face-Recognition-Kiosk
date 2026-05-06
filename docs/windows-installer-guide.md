# Windows App + Installer Flow

This project now includes a Windows packaging flow under `installer/windows`.

## What it does

- Installs required runtime dependencies with `winget` (Python + PostgreSQL).
- Creates a local virtual environment and installs `requirements.txt`.
- Runs first-time setup:
  - prompts for PostgreSQL connection values,
  - writes `DATABASE_URL` to `.env.local`,
  - auto-detects available camera indexes and prompts for `entry` and `exit` camera source indexes,
  - writes `ENTRY_CCTV_STREAM_SOURCE` and `EXIT_CCTV_STREAM_SOURCE` to `.env.local`,
  - creates the target database if missing,
  - runs `alembic upgrade head`,
  - prompts and creates initial `super_admin` credentials.
- Launches the app through `app.windows_launcher`:
  - starts `app.host_stack`,
  - opens browser automatically to the web app,
  - opens a separate developer log window tailing `%LOCALAPPDATA%\\FaceRecognitionKiosk\\logs\\host-stack.log`.

## Files added

- `scripts/first_run_setup.py`
- `app/windows_launcher.py`
- `installer/windows/setup.ps1`
- `installer/windows/launch-kiosk.bat`
- `installer/windows/FaceRecognitionKiosk.iss`

## Build the installer (Inno Setup)

1. Install Inno Setup Compiler.
2. Open `installer/windows/FaceRecognitionKiosk.iss`.
3. Build the script.
4. Run the generated `LibraryFaceAccessSystemInstaller.exe` as Administrator.

## Direct run without building installer

```powershell
powershell -ExecutionPolicy Bypass -File installer/windows/setup.ps1
```

## Daily launch after installation

Use desktop/start-menu shortcut, or:

```powershell
installer/windows/launch-kiosk.bat
```

Note: no actual winget/Postgres install was executed here.
