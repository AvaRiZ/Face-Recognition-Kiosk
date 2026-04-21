from __future__ import annotations

import argparse
import os
import sqlite3
from typing import Iterable

import psycopg
from psycopg import sql


TABLES = [
    "users",
    "staff_accounts",
    "recognition_log",
    "recognition_events",
    "app_settings",
    "audit_log",
    "imported_logs",
]


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


def _table_exists_sqlite(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _table_exists_postgres(conn: psycopg.Connection, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name=%s
            )
            """,
            (table,),
        )
        return bool(cur.fetchone()[0])


def _fetch_sqlite_rows(conn: sqlite3.Connection, table: str):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    columns = [desc[0] for desc in cur.description]
    return columns, rows


def _copy_rows_postgres(conn: psycopg.Connection, table: str, columns: list[str], rows: Iterable[tuple]):
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(columns))
    col_sql = ", ".join(columns)
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def _reset_postgres_sequences(conn: psycopg.Connection, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
                            AND (
                                    column_default LIKE 'nextval(%'
                                    OR is_identity = 'YES'
                            )
            """,
            (table,),
        )
        serial_columns = [row[0] for row in cur.fetchall()]

    if not serial_columns:
        return

    with conn.cursor() as cur:
        for column in serial_columns:
            cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (table, column))
            sequence_name = cur.fetchone()[0]
            if not sequence_name:
                continue

            cur.execute(
                sql.SQL("SELECT COALESCE(MAX({}), 0) FROM {}")
                .format(sql.Identifier(column), sql.Identifier(table))
            )
            max_value = int(cur.fetchone()[0] or 0)
            if max_value > 0:
                cur.execute("SELECT setval(%s, %s, true)", (sequence_name, max_value))
            else:
                cur.execute("SELECT setval(%s, %s, false)", (sequence_name, 1))


def migrate(sqlite_path: str, postgres_url: str) -> None:
    sqlite_conn = sqlite3.connect(sqlite_path)
    pg_conn = psycopg.connect(_normalize_postgres_url(postgres_url))

    migrated_tables = []
    for table in TABLES:
        if not _table_exists_sqlite(sqlite_conn, table):
            print(f"Skip (sqlite missing): {table}")
            continue
        if not _table_exists_postgres(pg_conn, table):
            print(f"Skip (postgres missing): {table}")
            continue

        cols, rows = _fetch_sqlite_rows(sqlite_conn, table)
        with pg_conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
        _copy_rows_postgres(pg_conn, table, cols, rows)
        _reset_postgres_sequences(pg_conn, table)
        migrated_tables.append((table, len(rows)))
        print(f"Migrated {table}: {len(rows)} rows")

    pg_conn.commit()

    print("\nValidation summary:")
    for table, sqlite_count in migrated_tables:
        with pg_conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            pg_count = int(cur.fetchone()[0])
        status = "OK" if pg_count == sqlite_count else "MISMATCH"
        print(f"- {table}: sqlite={sqlite_count}, postgres={pg_count} [{status}]")

    sqlite_conn.close()
    pg_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="One-time SQLite -> PostgreSQL migrator.")
    parser.add_argument("--sqlite-path", required=True, help="Path to SQLite database file.")
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("DATABASE_URL"),
        help="Target PostgreSQL DATABASE_URL (defaults to env DATABASE_URL).",
    )
    args = parser.parse_args()
    postgres_url = (args.postgres_url or "").strip()
    if not postgres_url:
        parser.error("--postgres-url is required (or set DATABASE_URL in environment).")
    migrate(args.sqlite_path, postgres_url)


if __name__ == "__main__":
    main()
