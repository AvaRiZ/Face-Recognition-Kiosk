from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote_plus

try:
    from scripts.ensure_postgres_db import ensure_database
    from scripts.provision_initial_admin import provision
except ModuleNotFoundError:
    repo_root_for_imports = Path(__file__).resolve().parent.parent
    if str(repo_root_for_imports) not in sys.path:
        sys.path.insert(0, str(repo_root_for_imports))
    from scripts.ensure_postgres_db import ensure_database
    from scripts.provision_initial_admin import provision


def _prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    if secret:
        value = getpass.getpass(f"{label}{suffix}: ").strip()
    else:
        value = input(f"{label}{suffix}: ").strip()
    if not value and default is not None:
        return default
    return value


def _detect_available_camera_indexes(max_index: int = 6) -> list[int]:
    try:
        import cv2  # Imported lazily so setup can still run if OpenCV import fails.
    except Exception:
        return []

    available: list[int] = []
    for index in range(max(0, int(max_index)) + 1):
        cap = None
        try:
            if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
                cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(index)
            if cap is not None and cap.isOpened():
                available.append(index)
        except Exception:
            pass
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
    return available


def _prompt_camera_index(label: str, default: str, available_indexes: list[int]) -> str:
    while True:
        value = _prompt(label, default)
        if not value.strip().isdigit():
            print("Please enter a numeric camera index (for example: 0, 1, 2).")
            continue
        selected = int(value.strip())
        if available_indexes and selected not in available_indexes:
            print(f"Camera index {selected} was not detected. Available: {available_indexes}")
            retry = _prompt("Use this index anyway? (y/N)", "N")
            if retry.strip().lower() not in {"y", "yes"}:
                continue
        return str(selected)


def _build_database_url(host: str, port: str, database: str, username: str, password: str) -> str:
    return (
        "postgresql+psycopg://"
        f"{quote_plus(username)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}"
    )


def _upsert_env_value(path: Path, key: str, value: str) -> None:
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated = False
    next_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            next_lines.append(f"{key}={value}")
            updated = True
        else:
            next_lines.append(line)

    if not updated:
        next_lines.append(f"{key}={value}")

    path.write_text("\n".join(next_lines).strip() + "\n", encoding="utf-8")


def _run_alembic_upgrade(repo_root: Path, database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(repo_root),
        env=env,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="First-run setup for Library Face Access System.")
    parser.add_argument("--db-host", default="localhost")
    parser.add_argument("--db-port", default="5432")
    parser.add_argument("--db-name", default="facerec_kiosk")
    parser.add_argument("--db-user", default="postgres")
    parser.add_argument("--db-password", default="")
    parser.add_argument("--admin-username", default="")
    parser.add_argument("--admin-full-name", default="System Administrator")
    parser.add_argument("--admin-password", default="")
    parser.add_argument("--entry-camera-index", default="0")
    parser.add_argument("--exit-camera-index", default="1")
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    db_host = args.db_host
    db_port = args.db_port
    db_name = args.db_name
    db_user = args.db_user
    db_password = args.db_password
    admin_username = args.admin_username
    admin_full_name = args.admin_full_name
    admin_password = args.admin_password
    entry_camera_index = str(args.entry_camera_index)
    exit_camera_index = str(args.exit_camera_index)

    if not args.non_interactive:
        print("\n=== Library Face Access System: First-Run Setup ===\n")
        db_host = _prompt("PostgreSQL host", db_host)
        db_port = _prompt("PostgreSQL port", db_port)
        db_name = _prompt("Application database name", db_name)
        db_user = _prompt("PostgreSQL username", db_user)
        if not db_password:
            db_password = _prompt("PostgreSQL password", secret=True)

        admin_username = _prompt("Initial superadmin username", admin_username or "superadmin")
        admin_full_name = _prompt("Initial superadmin full name", admin_full_name)
        if not admin_password:
            admin_password = _prompt("Initial superadmin password (min 8 chars)", secret=True)

        available_indexes = _detect_available_camera_indexes(max_index=6)
        if available_indexes:
            print(f"Detected camera indexes: {available_indexes}")
        else:
            print("No camera indexes were auto-detected. You can still set indexes manually.")
        entry_camera_index = _prompt_camera_index(
            "Entry camera index",
            entry_camera_index,
            available_indexes,
        )
        exit_camera_index = _prompt_camera_index(
            "Exit camera index",
            exit_camera_index,
            available_indexes,
        )

    if not db_password:
        raise ValueError("Database password is required.")
    if not admin_username:
        raise ValueError("Initial superadmin username is required.")
    if len(admin_password or "") < 8:
        raise ValueError("Initial superadmin password must be at least 8 characters.")

    database_url = _build_database_url(db_host, db_port, db_name, db_user, db_password)
    env_local_path = repo_root / ".env.local"

    print("\n[1/4] Saving DATABASE_URL to .env.local ...")
    _upsert_env_value(env_local_path, "DATABASE_URL", database_url)
    _upsert_env_value(env_local_path, "ALLOW_DEFAULT_ADMIN_BOOTSTRAP", "0")
    _upsert_env_value(env_local_path, "ENTRY_CCTV_STREAM_SOURCE", entry_camera_index)
    _upsert_env_value(env_local_path, "EXIT_CCTV_STREAM_SOURCE", exit_camera_index)

    os.environ["DATABASE_URL"] = database_url
    os.environ["ALLOW_DEFAULT_ADMIN_BOOTSTRAP"] = "0"

    print("[2/4] Ensuring application database exists ...")
    ensure_database(database_url)

    print("[3/4] Running Alembic migrations ...")
    _run_alembic_upgrade(repo_root, database_url)

    print("[4/4] Creating initial superadmin ...")
    try:
        provision(admin_username, admin_password, admin_full_name)
        print("Initial superadmin created successfully.")
    except RuntimeError as exc:
        if "already contains users" in str(exc):
            print("Skipped superadmin creation: staff accounts already exist.")
        else:
            raise

    print("\nSetup complete. You can now launch the Library Face Access System app.")


if __name__ == "__main__":
    main()
