import hashlib
import os
from functools import wraps
from flask import session, redirect, url_for, jsonify, request
from db import connect, resolve_database_target, table_columns

DB_PATH = resolve_database_target()

# -------------------------------
# Password hashing (no bcrypt needed)
# -------------------------------
def hash_password(password):
    """Hash password using SHA-256 with salt"""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ':' + key.hex()


def verify_password(stored_password, provided_password):
    """Verify a stored password against a provided password."""
    try:
        salt_hex, key_hex = stored_password.split(':')
        salt = bytes.fromhex(salt_hex)
        key = bytes.fromhex(key_hex)
        new_key = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return new_key == key
    except Exception:
        return False


def _default_admin_bootstrap_enabled():
    explicit = (os.environ.get("ALLOW_DEFAULT_ADMIN_BOOTSTRAP") or "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    app_env = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()
    return app_env in {"dev", "development", "local"}

# -------------------------------
# Database setup for staff accounts
# -------------------------------
def init_auth_db():
    """Validate auth tables and bootstrap default admin if enabled."""
    conn = connect(DB_PATH)
    c = conn.cursor()
    missing = []
    if not table_columns(conn, "staff_accounts"):
        missing.append("staff_accounts")
    if not table_columns(conn, "audit_log"):
        missing.append("audit_log")
    if missing:
        conn.close()
        raise RuntimeError(
            "PostgreSQL schema is missing authentication tables "
            f"{missing}. Run alembic upgrade head before starting the app."
        )

    conn.commit()
    c.execute("SELECT COUNT(*) FROM staff_accounts")
    count = c.fetchone()[0]
    if count == 0 and _default_admin_bootstrap_enabled():
        default_password = hash_password("password")
        c.execute(
            """
            INSERT INTO staff_accounts (username, password_hash, full_name, role)
            VALUES (%s, %s, %s, %s)
            """,
            ("admin", default_password, "System Administrator", "super_admin"),
        )
        conn.commit()
        print("[WARN] Default super admin created: username='admin', password='password'")
        print("[WARN] Change the default password immediately after first login.")
    elif count == 0:
        print(
            "[WARN] No staff accounts found and default bootstrap is disabled. "
            "Provision an initial super admin before first login."
        )

    conn.close()

# -------------------------------
# Auth functions
# -------------------------------
def login_user(username, password):
    """Authenticate user and return staff info or None"""
    conn = connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT staff_id, username, password_hash, full_name, role, is_active, profile_image
        FROM staff_accounts WHERE username = %s
    """, (username,))
    user = c.fetchone()

    if not user:
        conn.close()
        return None, "Invalid username or password"

    staff_id, uname, password_hash, full_name, role, is_active, profile_image = user

    if not is_active:
        conn.close()
        return None, "Account is deactivated. Contact your administrator."

    if not verify_password(password_hash, password):
        conn.close()
        return None, "Invalid username or password"

    # Update last login
    c.execute("UPDATE staff_accounts SET last_login = CURRENT_TIMESTAMP WHERE staff_id = %s", (staff_id,))

    # Log the login action
    ip = request.remote_addr if request else "unknown"
    c.execute("""
        INSERT INTO audit_log (staff_id, username, action, ip_address)
        VALUES (%s, %s, 'LOGIN', %s)
    """, (staff_id, uname, ip))

    conn.commit()
    conn.close()

    return {
        'staff_id': staff_id,
        'username': uname,
        'full_name': full_name,
        'role': role,
        'profile_image': profile_image
    }, None

def logout_user():
    """Log the logout action and clear session"""
    if 'staff_id' in session:
        conn = connect(DB_PATH)
        c = conn.cursor()
        ip = request.remote_addr if request else "unknown"
        c.execute("""
            INSERT INTO audit_log (staff_id, username, action, ip_address)
            VALUES (%s, %s, 'LOGOUT', %s)
        """, (session.get('staff_id'), session.get('username'), ip))
        conn.commit()
        conn.close()
    session.clear()

def log_action(action, target=None):
    """Log an admin/staff action to the audit log"""
    if 'staff_id' not in session:
        return
    conn = connect(DB_PATH)
    c = conn.cursor()
    ip = request.remote_addr if request else "unknown"
    c.execute("""
        INSERT INTO audit_log (staff_id, username, action, target, ip_address)
        VALUES (%s, %s, %s, %s, %s)
    """, (session['staff_id'], session['username'], action, target, ip))
    conn.commit()
    conn.close()

# -------------------------------
# Role decorators
# -------------------------------
def login_required(f):
    """Redirect to login if not authenticated"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'staff_id' not in session:
            return redirect(url_for('auth_routes.auth_login', next=request.path))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    """Return JSON 401 when request is not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'staff_id' not in session:
            return jsonify({"success": False, "message": "Authentication required."}), 401
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    """Allow access only to specified roles"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'staff_id' not in session:
                return redirect(url_for('auth_routes.auth_login', next=request.path))
            if session.get('role') not in roles:
                return redirect(url_for('auth_routes.unauthorized'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def api_role_required(*roles):
    """Return JSON 401/403 for API authorization checks."""
    allowed_roles = {str(role) for role in roles}

    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'staff_id' not in session:
                return jsonify({"success": False, "message": "Authentication required."}), 401
            if session.get('role') not in allowed_roles:
                return jsonify({"success": False, "message": "Forbidden."}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# -------------------------------
# Staff management functions
# -------------------------------
def get_all_staff():
    conn = connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT staff_id, username, full_name, role, is_active, created_at, last_login
        FROM staff_accounts ORDER BY created_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows

def create_staff(username, password, full_name, role):
    conn = connect(DB_PATH)
    c = conn.cursor()
    try:
        password_hash = hash_password(password)
        c.execute("""
            INSERT INTO staff_accounts (username, password_hash, full_name, role)
            VALUES (%s, %s, %s, %s)
        """, (username, password_hash, full_name, role))
        conn.commit()
        conn.close()
        return True, "Staff account created successfully"
    except Exception:
        conn.close()
        return False, "Username already exists"

def toggle_staff_status(staff_id):
    conn = connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        UPDATE staff_accounts
        SET is_active = CASE
            WHEN COALESCE(is_active, 0) = 0 THEN 1
            ELSE 0
        END
        WHERE staff_id = %s
        """,
        (staff_id,),
    )
    conn.commit()
    conn.close()

def change_password(staff_id, new_password):
    conn = connect(DB_PATH)
    c = conn.cursor()
    password_hash = hash_password(new_password)
    c.execute("UPDATE staff_accounts SET password_hash = %s WHERE staff_id = %s", (password_hash, staff_id))
    conn.commit()
    conn.close()

def get_staff_by_id(staff_id):
    conn = connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT staff_id, username, full_name, role, profile_image
        FROM staff_accounts
        WHERE staff_id = %s
    """, (staff_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "staff_id": row[0],
        "username": row[1],
        "full_name": row[2],
        "role": row[3],
        "profile_image": row[4],
    }

def verify_staff_password(staff_id, password):
    conn = connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM staff_accounts WHERE staff_id = %s", (staff_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    return verify_password(row[0], password)

def update_staff_profile(staff_id, full_name, username, profile_image=None):
    conn = connect(DB_PATH)
    c = conn.cursor()
    try:
        if profile_image is None:
            c.execute("""
                UPDATE staff_accounts
                SET full_name = %s, username = %s
                WHERE staff_id = %s
            """, (full_name, username, staff_id))
        else:
            c.execute("""
                UPDATE staff_accounts
                SET full_name = %s, username = %s, profile_image = %s
                WHERE staff_id = %s
            """, (full_name, username, profile_image, staff_id))
        conn.commit()
        conn.close()
        return True, "Profile updated successfully"
    except Exception:
        conn.close()
        return False, "Username already exists"

