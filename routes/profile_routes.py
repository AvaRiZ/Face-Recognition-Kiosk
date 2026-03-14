from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from auth import (
    change_password,
    get_staff_by_id,
    log_action,
    login_required,
    logout_user,
    role_required,
    update_staff_profile,
    verify_staff_password,
)
from services.staff_service import refresh_profile_session


def create_profile_blueprint(save_profile_image_fn):
    bp = Blueprint("profile_routes", __name__)

    @bp.route("/profile", endpoint="profile_settings")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def profile_settings():
        staff = get_staff_by_id(session.get("staff_id"))
        if not staff:
            logout_user()
            return redirect(url_for("auth_routes.auth_login"))
        return render_template("html/pages/profile-settings.html", staff=staff)

    @bp.route("/profile", methods=["POST"], endpoint="profile_settings_update")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def profile_settings_update():
        staff_id = session.get("staff_id")
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()

        if not full_name or not username:
            flash("Full name and username are required.", "error")
            return redirect(url_for("profile_routes.profile_settings"))

        profile_image = None
        file_storage = request.files.get("profile_image")
        if file_storage and file_storage.filename:
            profile_image, image_error = save_profile_image_fn(file_storage, staff_id)
            if image_error:
                flash(image_error, "error")
                return redirect(url_for("profile_routes.profile_settings"))

        success, message = update_staff_profile(staff_id, full_name, username, profile_image)
        if not success:
            flash(message, "error")
            return redirect(url_for("profile_routes.profile_settings"))

        updated = get_staff_by_id(staff_id)
        refresh_profile_session(session, updated)
        log_action("UPDATE_PROFILE", target=session["username"])
        flash("Profile information updated.", "success")
        return redirect(url_for("profile_routes.profile_settings"))

    @bp.route("/profile/password", methods=["POST"], endpoint="profile_change_password")
    @login_required
    @role_required("super_admin", "library_admin", "library_staff")
    def profile_change_password():
        staff_id = session.get("staff_id")
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_password or not new_password or not confirm_password:
            flash("All password fields are required.", "error")
            return redirect(url_for("profile_routes.profile_settings"))

        if not verify_staff_password(staff_id, current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("profile_routes.profile_settings"))

        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return redirect(url_for("profile_routes.profile_settings"))

        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
            return redirect(url_for("profile_routes.profile_settings"))

        change_password(staff_id, new_password)
        log_action("CHANGE_PASSWORD", target=session["username"])
        flash("Password updated successfully.", "success")
        return redirect(url_for("profile_routes.profile_settings"))

    return bp
