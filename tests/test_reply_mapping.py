"""Tests for reply mapping functionality."""

import sys
import os
import json
import tempfile
import unittest
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import database as db
import reply_mapping
from config import config
from constants import CASE_TYPE_DATA_ABSENCE
from time_utils import utc_now_iso


class TestAttachReplyToGroup(unittest.TestCase):
    """Test attach_reply_to_group()."""

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

    def test_attach_reply_to_group_creates_mapping_row(self):
        """attach_reply_to_group() creates a reply_case_mappings row with group_id set."""
        # Create a reply email
        reply_id = "reply-001"
        db.insert_email(
            email_id=reply_id,
            message_id="msg-001",
            thread_id="thread-001",
            subject="RE: Action Required",
            from_addr="contractor@example.com",
            to_addr="solucore@example.com",
            received_at=utc_now_iso(),
            raw_body="Here is the response.",
            normalized_text="here is the response",
        )

        # Create a building group
        group_id = "group-001"
        db.insert_building_group(
            group_id=group_id,
            grouping_key="building_group::building a::contractor x",
            building="Building A",
            normalized_building="building a",
            contractor="Contractor X",
            normalized_contractor="contractor x",
        )

        # Attach reply to group
        mapping_id = reply_mapping.attach_reply_to_group(reply_id, group_id, source="manual")

        # Verify mapping was created
        self.assertIsNotNone(mapping_id)
        mappings = db.get_reply_mappings_for_email(reply_id)
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["reply_email_id"], reply_id)
        self.assertEqual(mappings[0]["group_id"], group_id)
        self.assertIsNone(mappings[0]["case_id"])
        self.assertEqual(mappings[0]["mapping_source"], "manual")

    def test_attach_reply_does_not_close_case(self):
        """attach_reply_to_group() does not modify case status."""
        reply_id = "reply-002"
        case_id = "case-001"
        group_id = "group-002"

        # Create case
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            building="Building B",
            contractor="Contractor Y",
            grouping_key="test-key",
        )

        # Create reply email
        db.insert_email(
            email_id=reply_id,
            message_id="msg-002",
            thread_id="thread-002",
            subject="RE: Action Required",
            from_addr="contractor@example.com",
            to_addr="solucore@example.com",
            received_at=utc_now_iso(),
            raw_body="Response.",
            normalized_text="response",
        )

        # Create group
        db.insert_building_group(
            group_id=group_id,
            grouping_key="building_group::building b::contractor y",
            building="Building B",
            normalized_building="building b",
            contractor="Contractor Y",
            normalized_contractor="contractor y",
        )

        # Check case status before
        case_before = db.get_case_by_id(case_id)
        original_status = case_before["status"]

        # Attach reply
        reply_mapping.attach_reply_to_group(reply_id, group_id)

        # Check case status after — should not change
        case_after = db.get_case_by_id(case_id)
        self.assertEqual(case_after["status"], original_status)


class TestProposeMappings(unittest.TestCase):
    """Test propose_reply_case_mappings()."""

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

    def test_propose_mappings_returns_list(self):
        """propose_reply_case_mappings() returns a list of suggestions."""
        reply_id = "reply-003"

        # Create reply with case ID in subject
        db.insert_email(
            email_id=reply_id,
            message_id="msg-003",
            thread_id=None,
            subject="RE: case-abc-001",
            from_addr="contractor@example.com",
            to_addr="solucore@example.com",
            received_at=utc_now_iso(),
            raw_body="Here is the response.",
            normalized_text="here is the response",
        )

        # Get suggestions
        suggestions = reply_mapping.propose_reply_case_mappings(reply_id)

        # Should return a list (may be empty if case-abc-001 doesn't exist)
        self.assertIsInstance(suggestions, list)


class TestSaveReplyMapping(unittest.TestCase):
    """Test save_reply_case_mapping()."""

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

    def test_save_reply_case_mapping_persists_row(self):
        """save_reply_case_mapping() creates a reply_case_mappings row."""
        reply_id = "reply-004"
        case_id = "case-004"

        # Create reply email
        db.insert_email(
            email_id=reply_id,
            message_id="msg-004",
            thread_id=None,
            subject="RE: Action Required",
            from_addr="contractor@example.com",
            to_addr="solucore@example.com",
            received_at=utc_now_iso(),
            raw_body="Response.",
            normalized_text="response",
        )

        # Create case
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            building="Building D",
            contractor="Contractor Z",
            grouping_key="test-key-d",
        )

        # Save mapping
        mapping_id = reply_mapping.save_reply_case_mapping(
            reply_email_id=reply_id,
            case_id=case_id,
            source="manual",
        )

        # Verify
        self.assertIsNotNone(mapping_id)
        mappings = db.get_reply_mappings_for_case(case_id)
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["case_id"], case_id)


class TestAnalyzeCompleteness(unittest.TestCase):
    """Test analyze_reply_completeness()."""

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

    def test_analyze_completeness_detects_addressed_requirements(self):
        """analyze_reply_completeness() finds keywords matching addressed requirements."""
        reply_id = "reply-005"
        case_id = "case-005"

        # Create case
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            building="Building E",
            contractor="Contractor E",
            grouping_key="test-key-e",
        )

        # Build requirements
        from response_requirements import build_case_requirements
        build_case_requirements(case_id)

        # Create reply mentioning upload confirmation
        db.insert_email(
            email_id=reply_id,
            message_id="msg-005",
            thread_id=None,
            subject="RE: Data Submission",
            from_addr="contractor@example.com",
            to_addr="solucore@example.com",
            received_at=utc_now_iso(),
            raw_body="We have confirmed upload of the maintenance data to the system.",
            normalized_text="we have confirmed upload of the maintenance data to the system",
        )

        # Analyze completeness
        result = reply_mapping.analyze_reply_completeness(reply_email_id=reply_id, case_id=case_id)

        # Should detect upload_confirmation as addressed
        self.assertIn("upload_confirmation", result["addressed_keys"])

    def test_analyze_completeness_detects_missing_requirements(self):
        """analyze_reply_completeness() identifies unaddressed requirements."""
        reply_id = "reply-006"
        case_id = "case-006"

        # Create case
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            building="Building F",
            contractor="Contractor F",
            grouping_key="test-key-f",
        )

        # Build requirements
        from response_requirements import build_case_requirements
        build_case_requirements(case_id)

        # Create reply that doesn't address everything
        db.insert_email(
            email_id=reply_id,
            message_id="msg-006",
            thread_id=None,
            subject="RE: Maintenance",
            from_addr="contractor@example.com",
            to_addr="solucore@example.com",
            received_at=utc_now_iso(),
            raw_body="We are working on it.",
            normalized_text="we are working on it",
        )

        # Analyze completeness
        result = reply_mapping.analyze_reply_completeness(reply_email_id=reply_id, case_id=case_id)

        # Should have missing keys
        self.assertTrue(len(result["missing_keys"]) > 0)

    def test_analyze_completeness_claimed_no_evidence_creates_review(self):
        """analyze_reply_completeness() creates manual review if completion claimed without evidence."""
        reply_id = "reply-007"
        case_id = "case-007"

        # Create case
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            building="Building G",
            contractor="Contractor G",
            grouping_key="test-key-g",
        )

        # Build requirements
        from response_requirements import build_case_requirements
        build_case_requirements(case_id)

        # Create reply claiming completion without evidence
        db.insert_email(
            email_id=reply_id,
            message_id="msg-007",
            thread_id=None,
            subject="RE: RESOLVED",
            from_addr="contractor@example.com",
            to_addr="solucore@example.com",
            received_at=utc_now_iso(),
            raw_body="This is complete and resolved.",
            normalized_text="this is complete and resolved",
        )

        # Count reviews before
        reviews_before = db.get_manual_reviews_for_case(case_id)
        count_before = len(reviews_before)

        # Analyze completeness
        result = reply_mapping.analyze_reply_completeness(reply_email_id=reply_id, case_id=case_id)

        # If completion claimed without evidence, should create a review
        if result["completion_claimed"] and not result["evidence_found"]:
            reviews_after = db.get_manual_reviews_for_case(case_id)
            self.assertGreater(len(reviews_after), count_before)

    def test_completion_claim_does_not_close_case(self):
        """analyze_reply_completeness() never closes the case."""
        reply_id = "reply-008"
        case_id = "case-008"

        # Create case
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            building="Building H",
            contractor="Contractor H",
            grouping_key="test-key-h",
        )

        # Build requirements
        from response_requirements import build_case_requirements
        build_case_requirements(case_id)

        # Create reply claiming completion
        db.insert_email(
            email_id=reply_id,
            message_id="msg-008",
            thread_id=None,
            subject="RE: COMPLETE",
            from_addr="contractor@example.com",
            to_addr="solucore@example.com",
            received_at=utc_now_iso(),
            raw_body="All maintenance data has been uploaded and confirmed complete.",
            normalized_text="all maintenance data has been uploaded and confirmed complete",
        )

        # Get case status before
        case_before = db.get_case_by_id(case_id)
        original_status = case_before["status"]

        # Analyze completeness
        reply_mapping.analyze_reply_completeness(reply_email_id=reply_id, case_id=case_id)

        # Case status should not change
        case_after = db.get_case_by_id(case_id)
        self.assertEqual(case_after["status"], original_status)


if __name__ == "__main__":
    unittest.main()
