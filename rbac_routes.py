# ============================================================
# RBAC AUTH ROUTES — paste this into your app.py
# ============================================================
# 
# STEP 1: Add these imports at the top of app.py
# -----------------------------------------------
# from flask import session
# from auth import (
#     init_auth_db, login_user, logout_user, log_action,
#     login_required, role_required,
#     get_all_staff, create_staff, toggle_staff_status, change_password
# )
# import secrets
#
# STEP 2: Add secret key right after app = Flask(__name__)
# -----------------------------------------------
# app.secret_key = secrets.token_hex(32)
#
# STEP 3: Call init_auth_db() inside your existing init_db() function
# -----------------------------------------------
# def init_db():
#     ... existing code ...
#     init_auth_db()   # <-- add this line at the end
#
# STEP 4: Paste the routes below into app.py
# ============================================================


# ── Login / Logout ──────────────────────────────────────────

@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    # If already logged in, go to dashboard
    if 'staff_id' in session:
        return redirect(url_for('admin_dashboard'))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user, err = login_user(username, password)
        if user:
            session['staff_id']  = user['staff_id']
            session['username']  = user['username']
            session['full_name'] = user['full_name']
            session['role']      = user['role']
            return redirect(url_for('admin_dashboard'))
        else:
            error = err

    return render_template("login.html", error=error)


@app.route("/auth/logout")
def auth_logout():
    logout_user()
    return redirect(url_for('auth_login'))


# ── Unauthorized ─────────────────────────────────────────────

@app.route("/unauthorized")
def unauthorized():
    return render_template("unauthorized.html"), 403


# ── Admin Dashboard ───────────────────────────────────────────
# Accessible by: super_admin, library_admin

@app.route("/dashboard")
@login_required
@role_required('super_admin', 'library_admin')
def admin_dashboard():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Total registered students
    c.execute("SELECT COUNT(*) FROM users")
    total_students = c.fetchone()[0]

    # Total recognition events (canonical event model)
    c.execute("SELECT COUNT(*) FROM recognition_events")
    total_logs = c.fetchone()[0]

    # Today's recognitions (canonical event model)
    c.execute("""
        SELECT COUNT(*) FROM recognition_events
        WHERE DATE(captured_at) = DATE('now')
    """)
    today_logs = c.fetchone()[0]

    # Recent activity (last 10 events) - canonical event model
    import warnings
    warnings.warn(
        "Dashboard view uses recognition_events (canonical event model). "
        "See docs/database_schema_policy.md",
        DeprecationWarning,
        stacklevel=2
    )
    c.execute("""
        SELECT u.name, u.sr_code, re.confidence, re.captured_at
        FROM recognition_events re
        LEFT JOIN users u ON re.user_id = u.user_id
        WHERE re.captured_at IS NOT NULL
        ORDER BY re.captured_at DESC LIMIT 10
    """)
    recent_activity = c.fetchall()

    conn.close()

    return render_template("dashboard.html",
        total_students=total_students,
        total_logs=total_logs,
        today_logs=today_logs,
        recent_activity=recent_activity,
        staff_name=session.get('full_name'),
        staff_role=session.get('role')
    )


# ── Staff Management ──────────────────────────────────────────
# Accessible by: super_admin only

@app.route("/admin/staff")
@login_required
@role_required('super_admin')
def manage_staff():
    staff_list = get_all_staff()
    return render_template("staff_management.html",
        staff_list=staff_list,
        staff_name=session.get('full_name'),
        staff_role=session.get('role')
    )


@app.route("/admin/staff/create", methods=["POST"])
@login_required
@role_required('super_admin')
def create_staff_account():
    username  = request.form.get("username", "").strip()
    password  = request.form.get("password", "")
    full_name = request.form.get("full_name", "").strip()
    role      = request.form.get("role", "library_staff")

    if not all([username, password, full_name]):
        return redirect(url_for('manage_staff'))

    success, message = create_staff(username, password, full_name, role)
    if success:
        log_action("CREATE_STAFF", target=username)

    return redirect(url_for('manage_staff'))


@app.route("/admin/staff/toggle/<int:staff_id>", methods=["POST"])
@login_required
@role_required('super_admin')
def toggle_staff(staff_id):
    # Prevent super admin from deactivating themselves
    if staff_id == session.get('staff_id'):
        return {"error": "Cannot deactivate your own account"}, 400
    toggle_staff_status(staff_id)
    log_action("TOGGLE_STAFF_STATUS", target=str(staff_id))
    return redirect(url_for('manage_staff'))


# ── Student Management ────────────────────────────────────────
# Accessible by: super_admin, library_admin

@app.route("/admin/students")
@login_required
@role_required('super_admin', 'library_admin')
def manage_students():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT user_id, name, sr_code, course, created_at, last_updated
        FROM users ORDER BY created_at DESC
    """)
    students = c.fetchall()
    conn.close()
    return render_template("student_management.html",
        students=students,
        staff_name=session.get('full_name'),
        staff_role=session.get('role')
    )


# ── Logs / Entry-Exit ─────────────────────────────────────────
# Accessible by: super_admin, library_admin, library_staff

@app.route("/admin/logs")
@login_required
@role_required('super_admin', 'library_admin', 'library_staff')
def view_logs():
    """View recognition events (canonical model)."""
    import warnings
    warnings.warn(
        "View logs uses recognition_events (canonical event model). "
        "See docs/database_schema_policy.md",
        DeprecationWarning,
        stacklevel=2
    )
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT u.name, u.sr_code, re.confidence, re.captured_at
        FROM recognition_events re
        LEFT JOIN users u ON re.user_id = u.user_id
        WHERE re.captured_at IS NOT NULL
        ORDER BY re.captured_at DESC
        LIMIT 200
    """)
    logs = c.fetchall()
    conn.close()
    return render_template("logs.html",
        logs=logs,
        staff_name=session.get('full_name'),
        staff_role=session.get('role')
    )


# ── Protect existing sensitive routes ─────────────────────────

# Add @login_required and @role_required to your existing routes like this:
#
# @app.route("/settings", methods=["GET", "POST"])
# @login_required
# @role_required('super_admin')           <-- only super admin can change settings
# def settings():
#     ...
#
# @app.route("/api/reset_database", methods=["POST"])
# @login_required
# @role_required('super_admin')           <-- very dangerous, super admin only
# def reset_database():
#     ...