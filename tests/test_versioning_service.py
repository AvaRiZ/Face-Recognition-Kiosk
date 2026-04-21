from __future__ import annotations

import os
import tempfile
import unittest

from services.versioning_service import (
    bump_profiles_version,
    bump_settings_version,
    ensure_version_settings,
    get_profiles_version,
    get_settings_version,
)


class VersioningServiceTests(unittest.TestCase):
    def test_profile_and_settings_version_bump(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            ensure_version_settings(db_path)
            self.assertEqual(get_profiles_version(db_path), 1)
            self.assertEqual(get_settings_version(db_path), 1)

            self.assertEqual(bump_profiles_version(db_path), 2)
            self.assertEqual(bump_settings_version(db_path), 2)
            self.assertEqual(get_profiles_version(db_path), 2)
            self.assertEqual(get_settings_version(db_path), 2)


if __name__ == "__main__":
    unittest.main()
