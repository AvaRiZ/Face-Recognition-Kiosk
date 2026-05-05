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

## 6) Settings Runtime Behavior
Settings are stored in `app_settings` and exposed via `GET/POST /api/settings` (`/api/settings/recognition` alias).

Supported settings and bounds:
- `threshold` (0.1 to 0.95)
- `quality_threshold` (0.1 to 0.95)
- `recognition_confidence_threshold` (0.1 to 0.99)
- `vector_index_top_k` (1 to 100)
- `max_occupancy` (50 to 2000)
- `occupancy_warning_threshold` (0.5 to 0.99)
- `occupancy_snapshot_interval_seconds` (60 to 3600)
- `face_snapshot_retention_days` (1 to 365)
- `recognition_event_retention_days` (1 to 3650)
- `entry_cctv_stream_source` (text, non-empty)
- `exit_cctv_stream_source` (text, non-empty)

Role policy:
- `super_admin`: full edit access for all settings.
- `library_admin`: operational-only (`max_occupancy`, `vector_index_top_k`, `occupancy_warning_threshold`, `occupancy_snapshot_interval_seconds`).
- `library_staff`: read-only.

Apply timing:
- Live (no restart): thresholds, top-k, occupancy capacity/warning, occupancy snapshot interval, retention windows.
- Worker restart required: `entry_cctv_stream_source`, `exit_cctv_stream_source` (workers do not hot-reopen capture streams).

## 7) Internal Worker API Contract
- `POST /api/internal/recognition-events`
- `GET /api/internal/profiles/version`
- `GET /api/internal/profiles/snapshot`
- `GET /api/internal/runtime-config`
- `GET /api/internal/capacity-gate`
- `POST /api/internal/embedding-updates`
- `POST /api/internal/registration-samples`
- `POST /api/internal/worker-heartbeat`

`/api/internal/capacity-gate` is advisory-only (warning/monitoring). It does not block recognition or admission decisions.

Registration session start behavior:
- `/api/register-session/start` now requires a recent **entry worker heartbeat**.
- Default heartbeat TTL is `registration_worker_heartbeat_ttl_seconds` (10s).
- Worker sync loop sends heartbeat approximately every 3 seconds.

If `WORKER_INTERNAL_TOKEN` is set, worker requests must include:
- `Authorization: Bearer <token>`
