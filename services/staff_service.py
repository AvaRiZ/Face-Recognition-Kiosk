import os
import time
from werkzeug.utils import secure_filename


PROFILE_UPLOAD_DIR = os.path.join("static", "assets", "img", "profiles")
ALLOWED_PROFILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def ensure_profile_upload_dir():
    os.makedirs(PROFILE_UPLOAD_DIR, exist_ok=True)


def save_profile_image(file_storage, staff_id):
    if not file_storage or not file_storage.filename:
        return None, None

    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_PROFILE_EXTENSIONS:
        return None, "Only JPG, JPEG, PNG, and WEBP are allowed."

    safe_name = secure_filename(f"staff_{staff_id}_{int(time.time() * 1000)}{ext}")
    disk_path = os.path.join(PROFILE_UPLOAD_DIR, safe_name)
    file_storage.save(disk_path)
    web_path = f"/static/assets/img/profiles/{safe_name}"
    return web_path, None


def apply_login_session(session_obj, user):
    session_obj["staff_id"] = user["staff_id"]
    session_obj["username"] = user["username"]
    session_obj["full_name"] = user["full_name"]
    session_obj["role"] = user["role"]
    session_obj["profile_image"] = user.get("profile_image")


def refresh_profile_session(session_obj, staff):
    session_obj["full_name"] = staff["full_name"]
    session_obj["username"] = staff["username"]
    session_obj["profile_image"] = staff.get("profile_image")
