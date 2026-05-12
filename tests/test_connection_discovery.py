"""
Tests for connection_discovery.py — all offline with mocked AI transport.

Tests cover:
  1. Command refuses without --max-ai-calls
  2. Summaries include only six supported case types
  3. Unsupported / backlog records excluded from prompt
  4. Valid hypothesis stored
  5. Hypothesis referencing unsupported/nonexistent case ID rejected
  6. Hypothesis with no case evidence rejected
  7. Hypothesis recommending send/escalate/close rejected
  8. --dry-run produces no DB write
  9. AI calls through ai_gateway; budget enforced
  10. Observability event: unsupported_kpi_included = 0
  11. Existing demo/backlog/scale commands still pass
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ai_gateway
import database as db
import connection_discovery
from ai_gateway import AiUsageConfig, get_ai_gateway
from config import config
from connection_discovery import (
    _build_discovery_prompt,
    _build_supported_case_summaries,
    _validate_hypothesis,
    run_discovery,
)
from constants import (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    SUPPORTED_CASE_TYPES,
    VALID_HYPOTHESIS_CONFIDENCES,
    VALID_HYPOTHESIS_RISK_LEVELS,
)
from runtime_options import RuntimeOptions, runtime_options


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_supported_case(case_type: str, idx: int = 1) -> str:
    """Insert a supported case and return its case_id."""
    import uuid as _uuid
    case_id = f"test-case-{case_type.lower()}-{idx}"
    db.insert_case(
        case_id=case_id,
        case_type=case_type,
        grouping_key=f"gk-{case_type}-{idx}",
        building=f"Test Building {idx}",
        device=f"Device-{idx}",
        contractor="Test Contractor",
        due_date=None,
        period=None,
    )
    return case_id


def _valid_hypothesis_payload(case_ids: list) -> dict:
    """Return a valid AI hypothesis response dict."""
    return {
        "hypotheses": [
            {
                "hypothesis_type": "shared_contractor_pattern",
                "summary": "Possible connection between cases with the same contractor.",
                "confidence": "medium",
                "risk_level": "review",
                "evidence": {
                    "case_ids": case_ids,
                    "description": "Cases share a contractor and may be related.",
                },
                "reasoning": "Evidence indicates the same contractor appears across multiple cases.",
                "recommended_human_review": "Check whether the contractor's performance affects both cases.",
            }
        ]
    }


class TestConnectionDiscovery(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._orig_db_path = config.DATABASE_PATH
        self._orig_cache_path = config.CLAUDE_CACHE_PATH
        self._orig_report_path = config.AI_REPORT_PATH
        self._orig_obs_path = getattr(config, "OBSERVABILITY_LOG_PATH", None)

        config.DATABASE_PATH = Path(self.temp_dir.name) / "test_agent.db"
        config.CLAUDE_CACHE_PATH = Path(self.temp_dir.name) / "claude_cache.json"
        config.AI_REPORT_PATH = Path(self.temp_dir.name) / "ai_usage.json"
        config.OBSERVABILITY_LOG_PATH = Path(self.temp_dir.name) / "events.jsonl"

        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())

    def tearDown(self):
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())
        db.close_connection()
        config.DATABASE_PATH = self._orig_db_path
        config.CLAUDE_CACHE_PATH = self._orig_cache_path
        config.AI_REPORT_PATH = self._orig_report_path
        if self._orig_obs_path is not None:
            config.OBSERVABILITY_LOG_PATH = self._orig_obs_path
        self.temp_dir.cleanup()

    def _configure_gateway(self, max_calls: int = 5, transport=None):
        gateway = get_ai_gateway()
        gateway.reset()
        gateway.configure(
            AiUsageConfig(
                enabled=True,
                max_calls=max_calls,
                budget_mode="manual_review",
                model_name="claude-haiku-4-5-20251001",
                config_version="test-discover-connections",
            )
        )
        if transport is not None:
            gateway.set_test_transports(json_transport=transport)
        return gateway

    # -----------------------------------------------------------------------
    # Test 1: Command refuses without --max-ai-calls
    # -----------------------------------------------------------------------

    def test_command_refuses_without_max_ai_calls(self):
        """discover-connections exits non-zero when --max-ai-calls is 0 or missing."""
        import agent

        class Args:
            max_ai_calls = 0
            limit = None
            building = None
            case_type = None
            dry_run = False

        with self.assertRaises(SystemExit) as ctx:
            agent.cmd_discover_connections(Args())
        self.assertNotEqual(ctx.exception.code, 0)

    # -----------------------------------------------------------------------
    # Test 2: Summaries include only six supported case types
    # -----------------------------------------------------------------------

    def test_summaries_include_only_supported_case_types(self):
        """build_supported_case_summaries asserts all cases are supported."""
        from unittest.mock import MagicMock

        supported_case = MagicMock()
        supported_case.__getitem__ = lambda self, k: {
            "case_id": "c1",
            "case_type": CASE_TYPE_CAT1_COMPLIANCE,
            "status": "open",
            "building": "B1",
            "device": None,
            "contractor": None,
            "due_date": None,
            "period": None,
            "created_at": "2026-01-01T00:00:00",
        }[k]
        supported_case.keys = lambda: ["case_id", "case_type", "status", "building", "device", "contractor", "due_date", "period", "created_at"]

        # Use dict() style via real DB rows
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=2)
        cases = db.get_supported_cases_for_discovery()
        summaries = _build_supported_case_summaries(cases, {}, {}, {})
        case_types_in_summaries = {s["case_type"] for s in summaries}
        for ct in case_types_in_summaries:
            self.assertIn(ct, SUPPORTED_CASE_TYPES)

    # -----------------------------------------------------------------------
    # Test 3: Unsupported / backlog records excluded from prompt
    # -----------------------------------------------------------------------

    def test_unsupported_records_excluded_from_prompt(self):
        """get_supported_cases_for_discovery never returns UNKNOWN cases."""
        # Insert an unsupported UNKNOWN case directly
        db._execute_write(
            """
            INSERT INTO cases
                (case_id, case_type, status, priority, grouping_key,
                 building, device, contractor, due_date, period, created_at, updated_at)
            VALUES (?, ?, 'open', 'medium', ?, ?, NULL, NULL, NULL, NULL, ?, ?)
            """,
            ("unknown-case-1", "UNKNOWN", "gk-unknown-1", "Some Building",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        _insert_supported_case(CASE_TYPE_DATA_ABSENCE, idx=10)

        cases = db.get_supported_cases_for_discovery()
        case_types = [dict(c)["case_type"] for c in cases]
        self.assertNotIn("UNKNOWN", case_types)
        self.assertIn(CASE_TYPE_DATA_ABSENCE, case_types)

    # -----------------------------------------------------------------------
    # Test 4: Valid hypothesis stored
    # -----------------------------------------------------------------------

    def test_valid_hypothesis_stored(self):
        """A valid AI hypothesis is stored in connection_hypotheses."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=3)

        def mock_transport(prompt, model):
            return _valid_hypothesis_payload([case_id])

        gateway = self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(1, result["hypotheses_proposed"])
        self.assertEqual(0, result["hypotheses_rejected"])

        rows = db.get_connection_hypotheses()
        self.assertEqual(1, len(rows))
        hyp = dict(rows[0])
        self.assertEqual("proposed", hyp["status"])
        self.assertEqual("shared_contractor_pattern", hyp["hypothesis_type"])
        self.assertEqual("medium", hyp["confidence"])

        linked_cases = db.get_cases_for_hypothesis(hyp["hypothesis_id"])
        self.assertIn(case_id, linked_cases)

    # -----------------------------------------------------------------------
    # Test 5: Hypothesis referencing unsupported/nonexistent case ID rejected
    # -----------------------------------------------------------------------

    def test_hypothesis_with_nonexistent_case_id_rejected(self):
        """A hypothesis referencing a non-existent case_id is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=4)

        def mock_transport(prompt, model):
            return _valid_hypothesis_payload(["nonexistent-case-id-99"])

        self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])
        self.assertEqual(0, len(db.get_connection_hypotheses()))

    def test_hypothesis_with_unsupported_case_id_rejected(self):
        """A hypothesis referencing an UNKNOWN case is rejected."""
        # Insert unsupported case directly
        db._execute_write(
            """
            INSERT INTO cases
                (case_id, case_type, status, priority, grouping_key,
                 building, device, contractor, due_date, period, created_at, updated_at)
            VALUES (?, ?, 'open', 'medium', ?, ?, NULL, NULL, NULL, NULL, ?, ?)
            """,
            ("unsupported-case-1", "UNKNOWN", "gk-unknown-2", "Building X",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        _insert_supported_case(CASE_TYPE_DATA_ABSENCE, idx=11)

        def mock_transport(prompt, model):
            # Reference the UNKNOWN case; it won't be in valid_case_ids
            return _valid_hypothesis_payload(["unsupported-case-1"])

        self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    # -----------------------------------------------------------------------
    # Test 6: Hypothesis with no case evidence rejected
    # -----------------------------------------------------------------------

    def test_hypothesis_with_empty_case_ids_rejected(self):
        """A hypothesis with empty evidence.case_ids is rejected."""
        _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=5)

        def mock_transport(prompt, model):
            return {
                "hypotheses": [
                    {
                        "hypothesis_type": "no_evidence_test",
                        "summary": "Possible connection.",
                        "confidence": "low",
                        "risk_level": "info",
                        "evidence": {"case_ids": [], "description": "No cases"},
                        "reasoning": "Evidence indicates nothing specific.",
                        "recommended_human_review": "Needs review.",
                    }
                ]
            }

        self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    def test_hypothesis_with_missing_evidence_rejected(self):
        """A hypothesis missing the evidence field entirely is rejected."""
        _insert_supported_case(CASE_TYPE_DATA_ABSENCE, idx=12)

        def mock_transport(prompt, model):
            return {
                "hypotheses": [
                    {
                        "hypothesis_type": "missing_evidence_test",
                        "summary": "Possible connection.",
                        "confidence": "low",
                        "risk_level": "info",
                        "reasoning": "Evidence indicates nothing.",
                        "recommended_human_review": "Needs review.",
                    }
                ]
            }

        self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    # -----------------------------------------------------------------------
    # Test 7: Hypothesis recommending send/escalate/close rejected
    # -----------------------------------------------------------------------

    def test_hypothesis_with_send_action_rejected(self):
        """A hypothesis with 'send' in recommended_human_review is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=6)

        def mock_transport(prompt, model):
            return {
                "hypotheses": [
                    {
                        "hypothesis_type": "prohibited_action_test",
                        "summary": "Possible connection.",
                        "confidence": "medium",
                        "risk_level": "review",
                        "evidence": {"case_ids": [case_id], "description": "Evidence"},
                        "reasoning": "Evidence indicates a pattern.",
                        "recommended_human_review": "Send an alert email to the contractor.",
                    }
                ]
            }

        self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    def test_hypothesis_with_escalate_action_rejected(self):
        """A hypothesis with 'escalate' in reasoning is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_DATA_ABSENCE, idx=13)

        def mock_transport(prompt, model):
            return {
                "hypotheses": [
                    {
                        "hypothesis_type": "prohibited_action_test",
                        "summary": "Possible connection.",
                        "confidence": "low",
                        "risk_level": "info",
                        "evidence": {"case_ids": [case_id], "description": "Evidence"},
                        "reasoning": "Should escalate this to management immediately.",
                        "recommended_human_review": "Needs review.",
                    }
                ]
            }

        self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    def test_hypothesis_with_close_action_rejected(self):
        """A hypothesis with 'close' in summary is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=7)

        def mock_transport(prompt, model):
            return {
                "hypotheses": [
                    {
                        "hypothesis_type": "prohibited_action_test",
                        "summary": "Close the related cases immediately.",
                        "confidence": "high",
                        "risk_level": "management_review",
                        "evidence": {"case_ids": [case_id], "description": "Evidence"},
                        "reasoning": "Evidence indicates an urgent issue.",
                        "recommended_human_review": "Needs review.",
                    }
                ]
            }

        self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    # -----------------------------------------------------------------------
    # Test 8: --dry-run produces no DB write
    # -----------------------------------------------------------------------

    def test_dry_run_produces_no_db_write(self):
        """A dry-run invocation does not insert any rows into connection_hypotheses."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=8)

        def mock_transport(prompt, model):
            return _valid_hypothesis_payload([case_id])

        self._configure_gateway(transport=mock_transport)

        result = run_discovery(max_ai_calls=5, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(1, result["hypotheses_proposed"])
        self.assertEqual(0, len(db.get_connection_hypotheses()))

    # -----------------------------------------------------------------------
    # Test 9: AI calls through ai_gateway; budget enforced
    # -----------------------------------------------------------------------

    def test_ai_calls_go_through_gateway(self):
        """run_discovery uses the AI gateway; the call count is recorded."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=9)

        call_count = [0]

        def mock_transport(prompt, model):
            call_count[0] += 1
            return _valid_hypothesis_payload([case_id])

        gateway = self._configure_gateway(max_calls=5, transport=mock_transport)

        run_discovery(max_ai_calls=5)

        report = gateway.build_report()
        self.assertGreaterEqual(report["total_ai_calls"], 1)
        self.assertEqual(1, call_count[0])

    def test_gateway_budget_blocks_calls_when_ai_disabled(self):
        """run_discovery returns no hypotheses when the gateway blocks the call."""
        case_id = _insert_supported_case(CASE_TYPE_DATA_ABSENCE, idx=14)

        # Configure gateway with AI disabled
        gateway = get_ai_gateway()
        gateway.reset()
        gateway.configure(
            AiUsageConfig(
                enabled=False,
                max_calls=0,
                budget_mode="manual_review",
            )
        )

        result = run_discovery(max_ai_calls=5)

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertIn(result.get("ai_outcome"), ("blocked", None))

    # -----------------------------------------------------------------------
    # Test 10: Observability event: unsupported_kpi_included = 0
    # -----------------------------------------------------------------------

    def test_observability_event_unsupported_kpi_zero(self):
        """A discovery run writes a JSONL event with unsupported_kpi_included=0."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=10)

        def mock_transport(prompt, model):
            return _valid_hypothesis_payload([case_id])

        self._configure_gateway(transport=mock_transport)

        run_discovery(max_ai_calls=5)

        log_path = config.OBSERVABILITY_LOG_PATH
        self.assertTrue(log_path.exists(), "Observability log not written")

        events = []
        with open(log_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        discovery_events = [
            e for e in events
            if e.get("component") == "connection_discovery"
        ]
        self.assertGreater(len(discovery_events), 0, "No connection_discovery event found")

        for evt in discovery_events:
            self.assertEqual(0, evt.get("unsupported_kpi_included"),
                             f"Expected unsupported_kpi_included=0, got {evt}")

    # -----------------------------------------------------------------------
    # Test 11: Existing demo/backlog/scale commands still pass
    # -----------------------------------------------------------------------

    def test_existing_demo_scale_still_passes(self):
        """The offline demo harness still passes after schema additions."""
        from demo_scale_harness import ScaleTestOptions, run_demo_scale_test

        result = run_demo_scale_test(
            ScaleTestOptions(
                emails=10,
                seed=42,
                offline=True,
                disable_outbound_generation=True,
                enable_followups=False,
                report_dir=Path(self.temp_dir.name) / "test_runs",
                verbose=False,
            )
        )
        self.assertIn(result.overall_result, ("PASS", "PASS WITH WARNINGS"))

    # -----------------------------------------------------------------------
    # Test: missing hypothesis_type and empty summary rejected
    # -----------------------------------------------------------------------

    def test_hypothesis_missing_hypothesis_type_rejected(self):
        """A hypothesis with no hypothesis_type is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=30)

        def mock_transport(prompt, model):
            payload = _valid_hypothesis_payload([case_id])
            del payload["hypotheses"][0]["hypothesis_type"]
            return payload

        self._configure_gateway(transport=mock_transport)
        result = run_discovery(max_ai_calls=5)
        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    def test_hypothesis_empty_summary_rejected(self):
        """A hypothesis with an empty summary string is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=31)

        def mock_transport(prompt, model):
            payload = _valid_hypothesis_payload([case_id])
            payload["hypotheses"][0]["summary"] = ""
            return payload

        self._configure_gateway(transport=mock_transport)
        result = run_discovery(max_ai_calls=5)
        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    # -----------------------------------------------------------------------
    # Test: prohibited blame/conclusion language rejected
    # -----------------------------------------------------------------------

    def test_hypothesis_with_root_cause_language_rejected(self):
        """A hypothesis containing 'root cause' is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=32)

        def mock_transport(prompt, model):
            payload = _valid_hypothesis_payload([case_id])
            payload["hypotheses"][0]["reasoning"] = "This is the root cause of the outages."
            return payload

        self._configure_gateway(transport=mock_transport)
        result = run_discovery(max_ai_calls=5)
        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    def test_hypothesis_with_contractor_failure_language_rejected(self):
        """A hypothesis containing 'contractor failure' is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_DATA_ABSENCE, idx=33)

        def mock_transport(prompt, model):
            payload = _valid_hypothesis_payload([case_id])
            payload["hypotheses"][0]["summary"] = "Possible contractor failure across sites."
            return payload

        self._configure_gateway(transport=mock_transport)
        result = run_discovery(max_ai_calls=5)
        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    def test_hypothesis_with_confirmed_language_rejected(self):
        """A hypothesis containing 'confirmed' is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=34)

        def mock_transport(prompt, model):
            payload = _valid_hypothesis_payload([case_id])
            payload["hypotheses"][0]["reasoning"] = "Confirmed pattern across both buildings."
            return payload

        self._configure_gateway(transport=mock_transport)
        result = run_discovery(max_ai_calls=5)
        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    def test_hypothesis_with_notify_client_language_rejected(self):
        """A hypothesis containing 'notify client' in recommended_human_review is rejected."""
        case_id = _insert_supported_case(CASE_TYPE_DATA_ABSENCE, idx=35)

        def mock_transport(prompt, model):
            payload = _valid_hypothesis_payload([case_id])
            payload["hypotheses"][0]["recommended_human_review"] = "Notify client of the findings."
            return payload

        self._configure_gateway(transport=mock_transport)
        result = run_discovery(max_ai_calls=5)
        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(1, result["hypotheses_rejected"])

    # -----------------------------------------------------------------------
    # Test: observability event includes hypothesis counts
    # -----------------------------------------------------------------------

    def test_observability_event_includes_hypothesis_counts(self):
        """The discovery_run observability event includes proposed/rejected/stored counts."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=36)

        def mock_transport(prompt, model):
            return _valid_hypothesis_payload([case_id])

        self._configure_gateway(transport=mock_transport)
        run_discovery(max_ai_calls=5)

        log_path = config.OBSERVABILITY_LOG_PATH
        self.assertTrue(log_path.exists())

        events = []
        with open(log_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        discovery_events = [
            e for e in events if e.get("component") == "connection_discovery"
        ]
        self.assertGreater(len(discovery_events), 0)

        last = discovery_events[-1]
        self.assertIn("hypotheses_proposed", last)
        self.assertIn("hypotheses_rejected", last)
        self.assertIn("hypotheses_stored", last)
        self.assertEqual(1, last["hypotheses_proposed"])
        self.assertEqual(0, last["hypotheses_rejected"])
        self.assertEqual(1, last["hypotheses_stored"])

    def test_observability_event_dry_run_hypotheses_stored_zero(self):
        """In dry-run mode, hypotheses_stored is 0 even when hypotheses pass validation."""
        case_id = _insert_supported_case(CASE_TYPE_DATA_ABSENCE, idx=37)

        def mock_transport(prompt, model):
            return _valid_hypothesis_payload([case_id])

        self._configure_gateway(transport=mock_transport)
        run_discovery(max_ai_calls=5, dry_run=True)

        log_path = config.OBSERVABILITY_LOG_PATH
        events = []
        with open(log_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))

        discovery_events = [
            e for e in events if e.get("component") == "connection_discovery"
        ]
        last = discovery_events[-1]
        self.assertEqual(1, last["hypotheses_proposed"])
        self.assertEqual(0, last["hypotheses_stored"])

    # -----------------------------------------------------------------------
    # Validate-hypothesis unit tests (no DB required)
    # -----------------------------------------------------------------------

    def test_validate_hypothesis_valid(self):
        """A well-formed hypothesis with valid case IDs passes validation."""
        valid_ids = {"case-1", "case-2"}
        hyp = {
            "hypothesis_type": "shared_building",
            "summary": "Possible connection between cases.",
            "confidence": "medium",
            "risk_level": "review",
            "evidence": {"case_ids": ["case-1"], "description": "Same building."},
            "reasoning": "Evidence indicates both cases share the same building.",
            "recommended_human_review": "Needs review of building records.",
        }
        is_valid, reason = _validate_hypothesis(hyp, valid_ids)
        self.assertTrue(is_valid)
        self.assertEqual("", reason)

    def test_validate_hypothesis_missing_type(self):
        """Missing hypothesis_type produces a clear rejection."""
        valid_ids = {"case-1"}
        hyp = {
            "summary": "Possible connection.",
            "confidence": "low",
            "risk_level": "info",
            "evidence": {"case_ids": ["case-1"], "description": "x"},
            "reasoning": "Evidence indicates something.",
            "recommended_human_review": "Needs review.",
        }
        is_valid, reason = _validate_hypothesis(hyp, valid_ids)
        self.assertFalse(is_valid)
        self.assertIn("hypothesis_type", reason)

    def test_validate_hypothesis_empty_summary(self):
        """Empty summary produces a clear rejection."""
        valid_ids = {"case-1"}
        hyp = {
            "hypothesis_type": "test_type",
            "summary": "   ",
            "confidence": "low",
            "risk_level": "info",
            "evidence": {"case_ids": ["case-1"], "description": "x"},
            "reasoning": "Evidence indicates something.",
            "recommended_human_review": "Needs review.",
        }
        is_valid, reason = _validate_hypothesis(hyp, valid_ids)
        self.assertFalse(is_valid)
        self.assertIn("summary", reason)

    def test_validate_hypothesis_invalid_confidence(self):
        valid_ids = {"case-1"}
        hyp = {
            "hypothesis_type": "test_type",
            "summary": "Possible connection.",
            "confidence": "very_high",
            "risk_level": "review",
            "evidence": {"case_ids": ["case-1"], "description": "x"},
            "reasoning": "Evidence indicates something.",
            "recommended_human_review": "Needs review.",
        }
        is_valid, reason = _validate_hypothesis(hyp, valid_ids)
        self.assertFalse(is_valid)
        self.assertIn("confidence", reason)

    def test_validate_hypothesis_invalid_risk_level(self):
        valid_ids = {"case-1"}
        hyp = {
            "hypothesis_type": "test_type",
            "summary": "Possible connection.",
            "confidence": "low",
            "risk_level": "urgent",
            "evidence": {"case_ids": ["case-1"], "description": "x"},
            "reasoning": "Evidence indicates something.",
            "recommended_human_review": "Needs review.",
        }
        is_valid, reason = _validate_hypothesis(hyp, valid_ids)
        self.assertFalse(is_valid)
        self.assertIn("risk_level", reason)

    # -----------------------------------------------------------------------
    # Prompt scope header test
    # -----------------------------------------------------------------------

    def test_discovery_prompt_includes_scope_header(self):
        """The AI prompt always includes the unsupported_emails_excluded: true header."""
        case_id = _insert_supported_case(CASE_TYPE_CAT1_COMPLIANCE, idx=20)
        cases = db.get_supported_cases_for_discovery()
        summaries = _build_supported_case_summaries(cases, {}, {}, {})
        prompt = _build_discovery_prompt(summaries)
        self.assertIn('"unsupported_emails_excluded": true', prompt)
        self.assertIn(case_id, prompt)

    # -----------------------------------------------------------------------
    # Web endpoint smoke test
    # -----------------------------------------------------------------------

    def test_web_endpoint_returns_json(self):
        """The /connection-hypotheses.json endpoint returns valid JSON."""
        try:
            from web.app import app as flask_app
        except ImportError:
            self.skipTest("Flask not installed")

        flask_app.config["TESTING"] = True
        with flask_app.test_client() as client:
            response = client.get("/connection-hypotheses.json")
            self.assertEqual(200, response.status_code)
            data = json.loads(response.data)
            self.assertIn("hypotheses", data)
            self.assertIn("count", data)


if __name__ == "__main__":
    unittest.main()
