from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from auth import api_login_required, api_role_required, log_action
from core.config import AppConfig
from services.alert_service import AlertService


bp = Blueprint("alerts", __name__)


def _json_error(message: str, status: int):
    return jsonify({"success": False, "message": message}), status


@bp.route("/", methods=["GET"], endpoint="list_alerts")
@api_login_required
@api_role_required("super_admin", "library_admin", "library_staff")
def list_alerts():
    try:
        config = AppConfig()
        service = AlertService(config.db_path)
        active_only = str(request.args.get("active", "true")).strip().lower() not in {"0", "false", "no"}
        limit = int(request.args.get("limit", 50))
        limit = max(1, min(limit, 500))
        return jsonify({"success": True, "alerts": service.list_alerts(active_only=active_only, limit=limit)})
    except Exception as exc:
        return _json_error(f"Failed to fetch alerts: {str(exc)}", 500)


@bp.route("/<int:alert_id>/dismiss", methods=["POST"], endpoint="dismiss_alert")
@api_login_required
@api_role_required("super_admin", "library_admin", "library_staff")
def dismiss_alert(alert_id: int):
    try:
        config = AppConfig()
        service = AlertService(config.db_path)
        dismissed_by = str(session.get("username") or session.get("full_name") or "unknown")
        changed = service.dismiss_alert(alert_id, dismissed_by=dismissed_by)
        if not changed:
            return _json_error("Alert not found or already dismissed.", 404)
        log_action("DISMISS_OCCUPANCY_ALERT", target=f"alert:{alert_id}")
        return jsonify({"success": True, "alert_id": int(alert_id), "dismissed": True})
    except Exception as exc:
        return _json_error(f"Failed to dismiss alert: {str(exc)}", 500)

