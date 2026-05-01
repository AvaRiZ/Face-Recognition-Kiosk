# LAN Deployment Runbook (Host Stack: Web API + Worker)

## 1) Prerequisites
- Python environment with `requirements.txt` installed.
- PostgreSQL reachable from host.
- `DATABASE_URL` points to PostgreSQL.
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
   - This applies all pending migrations, including timestamp standardization and FK policy fixes (see [database_schema_policy.md](database_schema_policy.md))
4. If you have legacy SQLite data, run one-time copy:
   - PowerShell: `python scripts/migrate_sqlite_to_postgres.py --sqlite-path database/faces_improved.db --postgres-url "$env:DATABASE_URL"`
   - Bash/Zsh: `python scripts/migrate_sqlite_to_postgres.py --sqlite-path database/faces_improved.db --postgres-url "$DATABASE_URL"`


## 3) Quick Start (Recommended: Unified Host Stack)
1. Optional: create a persistent local env file:
   - `Copy-Item .env.local.example .env.local`
   - Edit `.env.local` and set your real `DATABASE_URL`.
2. If you do not use `.env.local`, set database URL for your shell (once per session):
   - PowerShell: `$env:DATABASE_URL = "postgresql://<user>:<password>@127.0.0.1:5432/facerec_kiosk"`
3. Start unified host stack:
    - `pwsh -File scripts/start_system.ps1`
   - Or (Python wrapper): `python scripts/start_system.py`
   - This now starts API + recognition worker together in one host process.

Initial admin provisioning:
- Default `admin/password` bootstrap is disabled unless `ALLOW_DEFAULT_ADMIN_BOOTSTRAP=1` in dev/local setups.
- For first-time production setup, create the initial super admin explicitly:
  - `python scripts/provision_initial_admin.py --username "<admin-user>" --password "<strong-password>" --full-name "<name>"`

Run in terminal: $env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/facerec_kiosk"
python scripts/start_system.py --Foreground

Important:
- Do not run `python scripts/start_system.ps1`; `.ps1` files must be launched by PowerShell.

Optional flags:
- `-EnvFile "C:\path\to\.env"` to load variables from a specific env file.
- `-DatabaseUrl "postgresql://..."` to pass DB URL directly.
- `-ApiPort 5050` to change API port.
- `-Foreground` to run the unified host stack in the current terminal and show full errors.
- `-SplitMode` to run API/worker as separate processes (advanced/debug only).
- `-ApiOnly` or `-WorkerOnly` (with `-SplitMode`) to launch one service for troubleshooting.

VS Code task shortcut:
- Run task: `Start System (API + Worker)`

## 4) Start Services Individually (if needed)
Only use this section for debugging. Registration capture requires the unified host stack.
For split launch via one command, use: `pwsh -File scripts/start_system.ps1 -SplitMode`.

Start API service (LAN-exposed):
- `pwsh -File scripts/start_api.ps1`
- Defaults:
   - `FLASK_RUN_HOST=0.0.0.0`
   - `FLASK_RUN_PORT=5000`

Other LAN devices can now access: `http://<host-ip>:5000`.

Start recognition worker (same host):
- `pwsh -File scripts/start_worker.ps1`
- Defaults:
   - `WORKER_API_BASE_URL=http://127.0.0.1:5000`
   - `WORKER_QUEUE_DIR=data/worker_queue`

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
- `POST /api/internal/embedding-updates`

If `WORKER_INTERNAL_TOKEN` is set, worker requests must include:
- `Authorization: Bearer <token>`
