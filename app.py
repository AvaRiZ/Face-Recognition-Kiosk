from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.flask_app import create_flask_app
from app.realtime import socketio
from app.runtime_settings import apply_app_settings, load_default_local_env
from auth import init_auth_db
from core.config import AppConfig
from core.state import AppStateManager
from database.repository import UserRepository
from database.schema import init_canonical_schema
from db import is_postgres_target, resolve_database_target
from routes.routes import init_imported_logs_table
from services.staff_service import ensure_profile_upload_dir
from utils.logging import log_header, log_step


@dataclass
class AppRuntime:
    config: AppConfig
    state: AppStateManager
    repository: UserRepository


def build_runtime() -> AppRuntime:
    config = AppConfig()
    ensure_profile_upload_dir()

    repository = UserRepository(config.db_path)
    repository.init_db()
    init_auth_db()
    init_imported_logs_table(config.db_path)
    init_canonical_schema(config.db_path)

    state = AppStateManager(config)
    state.load_users(repository.get_all_users())
    return AppRuntime(config=config, state=state, repository=repository)


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    load_default_local_env(repo_root)

    db_target = resolve_database_target(AppConfig().db_path)
    if not is_postgres_target(db_target):
        raise RuntimeError(
            "This architecture requires PostgreSQL as the persistent datastore. "
            "Set DATABASE_URL to a postgres://, postgresql://, or postgresql+<driver>:// target."
        )

    runtime = build_runtime()
    apply_app_settings(runtime.config, runtime.state)
    host = os.environ.get("FLASK_RUN_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}

    log_header("Library Entrance Face Recognition Web API")
    log_step(f"Database target: {db_target}")
    log_step(f"Users in database: {runtime.state.user_count}")
    log_step(f"Serving dashboard and API at http://{host}:{port}")

    app = create_flask_app(runtime.config, runtime.state, runtime.repository, cli=None)
    socketio.run(
        app,
        host=host,
        port=port,
        debug=debug,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
