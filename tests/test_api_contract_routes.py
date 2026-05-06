from __future__ import annotations

from pathlib import Path
import unittest


class ApiContractRouteTests(unittest.TestCase):
    def test_auth_contract_paths_exist(self) -> None:
        source = Path("routes/auth_routes.py").read_text(encoding="utf-8")
        self.assertIn('/api/login', source)
        self.assertIn('/api/logout', source)
        self.assertIn('/api/session', source)
        self.assertIn('/api/auth/login', source)
        self.assertIn('/api/auth/logout', source)
        self.assertIn('/api/auth/session', source)

    def test_internal_contract_paths_exist(self) -> None:
        source = Path("routes/internal_routes.py").read_text(encoding="utf-8")
        self.assertIn('/recognition-events', source)
        self.assertIn('/profiles/version', source)
        self.assertIn('/profiles/snapshot', source)
        self.assertIn('/runtime-config', source)
        self.assertIn('/embedding-updates', source)
        self.assertIn('/registration-samples', source)
        self.assertIn('/worker-heartbeat', source)

    def test_dashboard_contract_paths_exist(self) -> None:
        source = Path("routes/routes.py").read_text(encoding="utf-8")
        self.assertIn('/api/settings', source)
        self.assertIn('/api/settings/recognition', source)
        self.assertIn('/api/entry-logs', source)
        self.assertIn('/api/entry-exit-logs', source)
        self.assertIn('/api/events', source)
        self.assertIn('/api/analytics/daily-report', source)
        self.assertIn('/api/analytics/occupancy-trends', source)
        self.assertIn('/api/audit-log', source)
        self.assertIn('/api/profiles', source)
        self.assertIn('/api/profiles/<int:user_id>', source)
        self.assertNotIn('/registered-profiles/delete/<int:user_id>', source)

    def test_occupancy_contract_paths_exist(self) -> None:
        source = Path("routes/occupancy_routes.py").read_text(encoding="utf-8")
        self.assertIn('/current', source)
        self.assertIn('/history', source)
        self.assertIn('/summary', source)
        self.assertIn('/adjust', source)
        self.assertIn('/reset', source)

    def test_settings_threshold_help_text_matches_comparator_semantics(self) -> None:
        source = Path("frontend/src/pages/Settings.jsx").read_text(encoding="utf-8")
        self.assertIn("Higher values = stricter", source)
        self.assertIn("Lower values = more lenient", source)

    def test_dashboard_mentions_dual_camera_model(self) -> None:
        source = Path("frontend/src/pages/Dashboard.jsx").read_text(encoding="utf-8").lower()
        self.assertIn("dual-camera mode", source)
        self.assertNotIn("entry logs only", source)


if __name__ == "__main__":
    unittest.main()
