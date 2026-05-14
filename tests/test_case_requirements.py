"""Tests for case data requirements."""

import sys
import os
import tempfile
import unittest
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import database as db
from config import config
from response_requirements import build_case_requirements, calculate_case_completeness
from constants import CASE_TYPE_DATA_ABSENCE, CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL
from time_utils import utc_now_iso


class TestBuildCaseRequirements(unittest.TestCase):
    """Test build_case_requirements() DB-backed version."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._orig_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.temp_dir.name) / "test_agent.db"
        db.close_connection()
        db.init_schema()

    def tearDown(self):
        db.close_connection()
        config.DATABASE_PATH = self._orig_db_path
        self.temp_dir.cleanup()

    def test_build_case_requirements_upserts_rows_by_case_type(self):
        """Calling build_case_requirements creates rows for all required items."""
        # Create a DATA_ABSENCE case
        case_id = "case-001"
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            building="Building A",
            contractor="Contractor X",
            grouping_key="test-key",
        )

        # Call build_case_requirements
        requirements = build_case_requirements(case_id)

        # Should have 5 requirements for DATA_ABSENCE
        self.assertEqual(len(requirements), 5)
        self.assertTrue(all(r["case_id"] == case_id for r in requirements))
        self.assertTrue(all(r["status"] == "missing" for r in requirements))

        # Check specific keys exist
        keys = {r["requirement_key"] for r in requirements}
        self.assertIn("upload_confirmation", keys)
        self.assertIn("maintenance_activity_date", keys)

    def test_build_case_requirements_does_not_overwrite_provided(self):
        """Calling build_case_requirements twice preserves 'provided' status."""
        case_id = "case-002"
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            building="Building B",
            contractor="Contractor Y",
            grouping_key="test-key-2",
        )

        # First call: builds requirements
        requirements1 = build_case_requirements(case_id)
        self.assertEqual(len(requirements1), 5)

        # Manually mark one as provided
        db.update_case_requirement_status(
            case_id=case_id,
            requirement_key="upload_confirmation",
            status="provided",
            source="email_body",
        )

        # Second call: should preserve 'provided' status
        requirements2 = build_case_requirements(case_id)
        self.assertEqual(len(requirements2), 5)

        # Find the upload_confirmation requirement
        upload_req = next(
            (r for r in requirements2 if r["requirement_key"] == "upload_confirmation"),
            None,
        )
        self.assertIsNotNone(upload_req)
        self.assertEqual(upload_req["status"], "provided")

    def test_calculate_completeness_counts_correctly(self):
        """calculate_case_completeness returns accurate counts and percentages."""
        case_id = "case-003"
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL,
            building="Building C",
            contractor="Contractor Z",
            grouping_key="test-key-3",
        )

        # Build requirements (5 for MAINTENANCE_HOURS_SHORTFALL)
        build_case_requirements(case_id)

        # Mark 2 as provided
        db.update_case_requirement_status(case_id, "completed_hours", "provided")
        db.update_case_requirement_status(case_id, "shortfall_reason", "provided")

        # Calculate completeness
        completeness = calculate_case_completeness(case_id)

        self.assertEqual(completeness["total"], 5)
        self.assertEqual(completeness["completed"], 2)
        self.assertAlmostEqual(completeness["percentage"], 40.0, places=1)
        self.assertIn("missing_time_records", completeness["missing_keys"])
        self.assertEqual(len(completeness["provided_keys"]), 2)


if __name__ == "__main__":
    unittest.main()
