"""Occupancy endpoints - real-time occupancy tracking and history.

Endpoints:
- GET  /api/occupancy/current         — Get current occupancy and capacity status
- GET  /api/occupancy/history         — Get occupancy snapshots for a date
- POST /api/internal/occupancy-snapshot — Create occupancy snapshot (internal trigger)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from flask import Blueprint, jsonify, request

from core.config import AppConfig
from services.occupancy_service import OccupancyService


bp = Blueprint("occupancy", __name__)


def _json_error(message: str, status: int):
    """Format error response."""
    return jsonify({"success": False, "message": message}), status


@bp.route("/current", methods=["GET"], endpoint="get_current")
def get_current() -> tuple:
    """
    Get current occupancy and capacity status.

    Response (< 100ms target):
    {
        "success": true,
        "occupancy_count": 87,
        "capacity_limit": 300,
        "occupancy_ratio": 0.290,
        "is_full": false,
        "capacity_warning": false,
        "daily_entries": 102,
        "daily_exits": 15,
        "timestamp_utc": "2026-05-02T15:30:45.123456+00:00"
    }
    """
    try:
        config = AppConfig()
        service = OccupancyService(config.db_path)

        occ_data = service.get_current_occupancy(config.max_library_capacity)

        return jsonify({
            "success": True,
            "occupancy_count": occ_data["occupancy_count"],
            "capacity_limit": occ_data["capacity_limit"],
            "occupancy_ratio": occ_data["occupancy_ratio"],
            "is_full": occ_data["is_full"],
            "capacity_warning": occ_data["capacity_warning"],
            "daily_entries": occ_data["daily_entries"],
            "daily_exits": occ_data["daily_exits"],
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }), 200

    except Exception as exc:
        return _json_error(f"Failed to fetch occupancy: {str(exc)}", 500)


@bp.route("/history", methods=["GET"], endpoint="get_history")
def get_history() -> tuple:
    """
    Get occupancy snapshots for a specific date (default: today).

    Query params:
    - date: ISO format date string (optional, default: today UTC)
    - limit: max snapshots to return (optional, default: 288 = 5-min intervals for 24h)

    Response:
    {
        "success": true,
        "date": "2026-05-02",
        "snapshots": [
            {
                "snapshot_timestamp": "2026-05-02T23:59:00Z",
                "occupancy_count": 95,
                "capacity_limit": 300,
                "capacity_warning": false,
                "daily_entries": 150,
                "daily_exits": 55
            },
            ...
        ]
    }
    """
    try:
        config = AppConfig()
        service = OccupancyService(config.db_path)

        date_param = request.args.get("date", type=str)
        limit_param = request.args.get("limit", default=288, type=int)

        # Parse date or default to today
        target_date = None
        if date_param:
            try:
                target_date = datetime.fromisoformat(date_param).date()
            except (ValueError, TypeError):
                return _json_error(
                    f"Invalid date format '{date_param}'. Use ISO format (YYYY-MM-DD).",
                    400,
                )

        history = service.get_history(target_date, limit=limit_param)

        return jsonify({
            "success": True,
            "date": (target_date or date.today()).isoformat(),
            "snapshot_count": len(history),
            "snapshots": [
                {
                    "snapshot_timestamp": snap["snapshot_timestamp"],
                    "occupancy_count": snap["occupancy_count"],
                    "capacity_limit": snap["capacity_limit"],
                    "capacity_warning": snap["capacity_warning"],
                    "daily_entries": snap["daily_entries"],
                    "daily_exits": snap["daily_exits"],
                }
                for snap in history
            ],
        }), 200

    except Exception as exc:
        return _json_error(f"Failed to fetch history: {str(exc)}", 500)


@bp.route("/summary", methods=["GET"], endpoint="get_summary")
def get_summary() -> tuple:
    """
    Get end-of-day occupancy summary (peak count, total entries/exits, warnings).

    Query params:
    - date: ISO format date string (optional, default: today UTC)

    Response:
    {
        "success": true,
        "date": "2026-05-02",
        "daily_entries": 150,
        "daily_exits": 55,
        "net_occupancy": 95,
        "peak_occupancy": 120,
        "capacity_warnings_count": 3
    }
    """
    try:
        config = AppConfig()
        service = OccupancyService(config.db_path)

        date_param = request.args.get("date", type=str)

        # Parse date or default to today
        target_date = None
        if date_param:
            try:
                target_date = datetime.fromisoformat(date_param).date()
            except (ValueError, TypeError):
                return _json_error(
                    f"Invalid date format '{date_param}'. Use ISO format (YYYY-MM-DD).",
                    400,
                )

        summary = service.get_daily_summary(target_date)

        return jsonify({
            "success": True,
            "date": summary["date_str"],
            "daily_entries": summary["daily_entries"],
            "daily_exits": summary["daily_exits"],
            "net_occupancy": summary["net_occupancy"],
            "peak_occupancy": summary["peak_occupancy"],
            "capacity_warnings_count": summary["capacity_warnings_count"],
        }), 200

    except Exception as exc:
        return _json_error(f"Failed to fetch summary: {str(exc)}", 500)
