# LAN Deployment Runbook (Python-First Host Stack)

## 1) Prerequisites
- Python environment with `requirements.txt` installed.
- PostgreSQL reachable from host.
- `DATABASE_URL` points to PostgreSQL.
- PostgreSQL CLI tools available on PATH for backup/restore checks (`pg_dump`, `pg_restore`, `psql`).
- Optional internal worker token:
  - `WORKER_INTERNAL_TOKEN=<strong-random-token>`

## 2) Database Migration
1. Set PostgreSQL connection URL for this shell:
   - PowerShell: `$env:DATABASE_URL = "postgresql://<user>:<password>@127.0.0.1:5432/facerec_kiosk"`
   - Bash/Zsh: `export DATABASE_URL="postgresql://<user>:<password>@127.0.0.1:5432/facerec_kiosk"`
2. Ensure target database exists:
   - `python scripts/ensure_postgres_db.py`
3. Apply canonical schema with Alembic:
   - `alembic upgrade head`
   - This applies all pending migrations, including timestamp standardization and FK policy fixes (see [database_schema_policy.md](database_schema_policy.md)).

## 3) Start Application (Canonical)
1. Optional: create a persistent local env file:
   - `Copy-Item .env.local.example .env.local`
   - Edit `.env.local` and set your real `DATABASE_URL`.
2. Start unified host stack:
   - `python -m app.host_stack`
3. Host stack behavior:
   - Serves web API and dashboard.
   - Starts entry and exit worker processes.
   - Auto-loads `.env.local` if present.

Example:
```powershell
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/facerec_kiosk"
python -m app.host_stack
```

Initial admin provisioning:
- Default `admin/password` bootstrap is disabled unless `ALLOW_DEFAULT_ADMIN_BOOTSTRAP=1` in dev/local setups.
- For first-time production setup, create the initial super admin explicitly:
  - `python scripts/provision_initial_admin.py --username "<admin-user>" --password "<strong-password>" --full-name "<name>"`

## 4) Debug Commands (Direct Python)
API-only:
- `python app.py`

Worker-only:
1. Set worker route env vars:
   - Entry worker:
     - PowerShell:
       - `$env:WORKER_ROLE="entry"`
       - `$env:WORKER_STATION_ID="entry-station-1"`
       - `$env:WORKER_CAMERA_ID="1"`
       - `$env:WORKER_CCTV_STREAM_SOURCE="0"`
   - Exit worker:
     - PowerShell:
       - `$env:WORKER_ROLE="exit"`
       - `$env:WORKER_STATION_ID="exit-station-1"`
       - `$env:WORKER_CAMERA_ID="2"`
       - `$env:WORKER_CCTV_STREAM_SOURCE="1"`
2. Run worker:
   - `python -m workers.recognition_worker`

Notes:
- Worker defaults are entry-oriented if role env vars are omitted.
- Set `WORKER_API_BASE_URL` when API is not running at `http://127.0.0.1:5000`.

## 5) Backup/Restore Verification
1. Backup:
   - `pwsh -File scripts/backup_postgres.ps1 -OutputFile backups\kiosk.backup`
2. Restore verification on a target DB:
   - `pwsh -File scripts/restore_verify_postgres.ps1 -BackupFile backups\kiosk.backup -RestoreUrl "<postgres-url>"`

## 6) Internal Worker API Contract
- `POST /api/internal/recognition-events`
- `GET /api/internal/profiles/version`
- `GET /api/internal/profiles/snapshot`
- `GET /api/internal/runtime-config`
- `GET /api/internal/capacity-gate`
- `POST /api/internal/embedding-updates`

If `WORKER_INTERNAL_TOKEN` is set, worker requests must include:
- `Authorization: Bearer <token>`
