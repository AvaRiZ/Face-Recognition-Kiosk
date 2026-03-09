import sqlite3
import hashlib
import os
from functools import wraps
from flask import session, redirect, url_for, jsonify, request

DB_PATH = "database/faces_improved.db"

# -------------------------------
# Password hashing (no bcrypt needed)
# -------------------------------
def hash_password(password):
    """Hash password using SHA-256 with salt"""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ':' + key.hex()

def verify_password(stored_password, provided_password):
    """Verify a stored password against a provided password"""
    try:
        salt_hex, key_hex = stored_password.split(':')
        salt = bytes.fromhex(salt_hex)
        key = bytes.fromhex(key_hex)
        new_key = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt, 100000)
        return new_key == key
    except Exception:
        return False

# -------------------------------
# Database setup for staff accounts
# -------------------------------
def init_auth_db():
    """Create staff_accounts and audit_log tables"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Staff accounts table
    c.execute('''
        CREATE TABLE IF NOT EXISTS staff_accounts (
            staff_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name   TEXT NOT NULL,
            role        TEXT NOT NULL CHECK(role IN ('super_admin', 'library_admin', 'library_staff')),
            is_active   INTEGER DEFAULT 1,
            profile_image TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login  TIMESTAMP
        )
    ''')

    # Backward-compatible migration for existing databases
    c.execute("PRAGMA table_info(staff_accounts)")
    existing_columns = {row[1] for row in c.fetchall()}
    if "profile_image" not in existing_columns:
        c.execute("ALTER TABLE staff_accounts ADD COLUMN profile_image TEXT")

    # Audit log table - tracks every admin/staff action
    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id    INTEGER,
            username    TEXT,
            action      TEXT NOT NULL,
            target      TEXT,
            ip_address  TEXT,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (staff_id) REFERENCES staff_accounts(staff_id)
        )
    ''')

    conn.commit()

    # Create default super admin if no accounts exist
    c.execute("SELECT COUNT(*) FROM staff_accounts")
    count = c.fetchone()[0]
    if count == 0:
        default_password = hash_password("password")
        c.execute("""
            INSERT INTO staff_accounts (username, password_hash, full_name, role)
            VALUES (?, ?, ?, ?)
        """, ("admin", default_password, "System Administrator", "super_admin"))
        conn.commit()
        print("✓ Default super admin created: username='superadmin', password='password'")
        print("  ⚠️  Please change the default password after first login!")

    conn.close()

# -------------------------------
# Auth functions
# -------------------------------
def login_user(username, password):
    """Authenticate user and return staff info or None"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT staff_id, username, password_hash, full_name, role, is_active, profile_image
        FROM staff_accounts WHERE username = ?
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
    c.execute("UPDATE staff_accounts SET last_login = CURRENT_TIMESTAMP WHERE staff_id = ?", (staff_id,))

    # Log the login action
    ip = request.remote_addr if request else "unknown"
    c.execute("""
        INSERT INTO audit_log (staff_id, username, action, ip_address)
        VALUES (?, ?, 'LOGIN', ?)
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
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        ip = request.remote_addr if request else "unknown"
        c.execute("""
            INSERT INTO audit_log (staff_id, username, action, ip_address)
            VALUES (?, ?, 'LOGOUT', ?)
        """, (session.get('staff_id'), session.get('username'), ip))
        conn.commit()
        conn.close()
    session.clear()

def log_action(action, target=None):
    """Log an admin/staff action to the audit log"""
    if 'staff_id' not in session:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    ip = request.remote_addr if request else "unknown"
    c.execute("""
        INSERT INTO audit_log (staff_id, username, action, target, ip_address)
        VALUES (?, ?, ?, ?, ?)
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

# -------------------------------
# Staff management functions
# -------------------------------
def get_all_staff():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT staff_id, username, full_name, role, is_active, created_at, last_login
        FROM staff_accounts ORDER BY created_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows

def create_staff(username, password, full_name, role):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        password_hash = hash_password(password)
        c.execute("""
            INSERT INTO staff_accounts (username, password_hash, full_name, role)
            VALUES (?, ?, ?, ?)
        """, (username, password_hash, full_name, role))
        conn.commit()
        conn.close()
        return True, "Staff account created successfully"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Username already exists"

def toggle_staff_status(staff_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE staff_accounts SET is_active = NOT is_active WHERE staff_id = ?", (staff_id,))
    conn.commit()
    conn.close()

def change_password(staff_id, new_password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    password_hash = hash_password(new_password)
    c.execute("UPDATE staff_accounts SET password_hash = ? WHERE staff_id = ?", (password_hash, staff_id))
    conn.commit()
    conn.close()

def get_staff_by_id(staff_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT staff_id, username, full_name, role, profile_image
        FROM staff_accounts
        WHERE staff_id = ?
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM staff_accounts WHERE staff_id = ?", (staff_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    return verify_password(row[0], password)

def update_staff_profile(staff_id, full_name, username, profile_image=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        if profile_image is None:
            c.execute("""
                UPDATE staff_accounts
                SET full_name = ?, username = ?
                WHERE staff_id = ?
            """, (full_name, username, staff_id))
        else:
            c.execute("""
                UPDATE staff_accounts
                SET full_name = ?, username = ?, profile_image = ?
                WHERE staff_id = ?
            """, (full_name, username, profile_image, staff_id))
        conn.commit()
        conn.close()
        return True, "Profile updated successfully"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Username already exists"
