import os
from typing import Optional

try:
    import psycopg
except Exception:  # pragma: no cover - optional dependency until installed
    psycopg = None


def resolve_database_target(db_path: Optional[str] = None) -> str:
    return os.environ.get("DATABASE_URL") or db_path or ""


def is_postgres_target(db_path: Optional[str] = None) -> bool:
    target = str(resolve_database_target(db_path)).strip().lower()
    return target.startswith(("postgres://", "postgresql://", "postgres+", "postgresql+"))


def _normalize_postgres_dsn_for_driver(target: str) -> str:
    """Normalize PostgreSQL DSN by removing the driver specifier if present."""
    normalized = str(target).strip()
    if "://" not in normalized:
        return normalized
    scheme, rest = normalized.split("://", 1)
    if "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    return f"{scheme}://{rest}"


def connect(db_path: Optional[str] = None):
    """Connect to the PostgreSQL database configured by DATABASE_URL."""
    target = resolve_database_target(db_path)
    if not is_postgres_target(target):
        raise RuntimeError(
            "PostgreSQL is required. Set DATABASE_URL to a postgres://, "
            "postgresql://, or postgresql+<driver>:// target."
        )
    if psycopg is None:
        raise RuntimeError("PostgreSQL selected but `psycopg` is not installed.")
    return psycopg.connect(_normalize_postgres_dsn_for_driver(target))


def table_columns(conn, table_name: str) -> set[str]:
    """Get the set of column names in a table."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    rows = cur.fetchall()
    return {row[0] for row in rows}


def get_app_setting(db_path: Optional[str], key: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch a single app_settings value with a safe fallback."""
    try:
        conn = connect(db_path)
    except Exception:
        return default

    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default
    finally:
        try:
            conn.close()
        except Exception:
            pass
