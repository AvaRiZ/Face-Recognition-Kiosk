from __future__ import annotations

import argparse

from auth import DB_PATH, hash_password, init_auth_db
from db import connect as db_connect


def provision(username: str, password: str, full_name: str) -> None:
    username = username.strip()
    full_name = full_name.strip()
    if not username:
        raise ValueError("username is required")
    if not full_name:
        raise ValueError("full_name is required")
    if len(password or "") < 8:
        raise ValueError("password must be at least 8 characters")

    conn = db_connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM staff_accounts")
    existing_count = int(c.fetchone()[0] or 0)
    if existing_count > 0:
        conn.close()
        raise RuntimeError(
            "staff_accounts already contains users. "
            "Use dashboard manage-users or direct admin workflows instead."
        )

    password_hash = hash_password(password)
    c.execute(
        """
        INSERT INTO staff_accounts (username, password_hash, full_name, role)
        VALUES (?, ?, ?, 'super_admin')
        """,
        (username, password_hash, full_name),
    )
    conn.commit()
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision the initial super admin account.")
    parser.add_argument("--username", required=True, help="Initial super admin username.")
    parser.add_argument("--password", required=True, help="Initial super admin password.")
    parser.add_argument(
        "--full-name",
        default="System Administrator",
        help="Display name for the initial super admin.",
    )
    args = parser.parse_args()
    init_auth_db()
    provision(args.username, args.password, args.full_name)
    print("Initial super admin account created successfully.")


if __name__ == "__main__":
    main()
