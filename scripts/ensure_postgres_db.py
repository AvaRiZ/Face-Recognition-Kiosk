from __future__ import annotations

import argparse
import os

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


def _normalize_postgres_url(url: str) -> str:
    normalized = url.strip()
    if "://" not in normalized:
        return normalized
    scheme, rest = normalized.split("://", 1)
    if "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    if scheme == "postgres":
        scheme = "postgresql"
    return f"{scheme}://{rest}"


def ensure_database(postgres_url: str, admin_dbname: str = "postgres") -> None:
    target_url = _normalize_postgres_url(postgres_url)
    parsed = conninfo_to_dict(target_url)
    target_dbname = (parsed.get("dbname") or "").strip()
    if not target_dbname:
        raise ValueError("Target database name is missing in connection URL.")

    with psycopg.connect(target_url, dbname=admin_dbname, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_dbname,))
            if cur.fetchone():
                print(f"Database already exists: {target_dbname}")
                return

            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_dbname)))
            print(f"Database created: {target_dbname}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure target PostgreSQL database exists (create if missing)."
    )
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("DATABASE_URL"),
        help="Target PostgreSQL DATABASE_URL (defaults to env DATABASE_URL).",
    )
    parser.add_argument(
        "--admin-dbname",
        default="postgres",
        help="Maintenance DB used for creation checks (default: postgres).",
    )
    args = parser.parse_args()

    postgres_url = (args.postgres_url or "").strip()
    if not postgres_url:
        parser.error("--postgres-url is required (or set DATABASE_URL in environment).")

    ensure_database(postgres_url, admin_dbname=args.admin_dbname)


if __name__ == "__main__":
    main()
