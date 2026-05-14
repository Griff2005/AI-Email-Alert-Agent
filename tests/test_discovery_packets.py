"""Phase 4 connection discovery packet tests.

All model usage is mocked through ai_gateway; no live AI calls are made.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ai_gateway
import database as db
from ai_gateway import AiUsageConfig, get_ai_gateway
from config import config
from constants import (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    CASE_TYPE_UNKNOWN,
)
from runtime_options import RuntimeOptions, runtime_options


def _valid_hypothesis(case_ids):
    return {
        "hypotheses": [
            {
                "hypothesis_type": "cross_case_type_relationship",
                "summary": "Possible connection between supported cases.",
                "confidence": "medium",
                "risk_level": "review",
                "evidence": {
                    "case_ids": case_ids,
                    "description": "Supported cases appear in the same packet.",
                },
                "reasoning": "Evidence indicates a reviewable relationship across the packet.",
                "recommended_human_review": "Review whether the relationship is meaningful.",
            }
        ]
    }


class DiscoveryPacketsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.original_database_path = config.DATABASE_PATH
        self.original_cache_path = config.CLAUDE_CACHE_PATH
        self.original_report_path = config.AI_REPORT_PATH
        self.original_observability_path = getattr(config, "OBSERVABILITY_LOG_PATH", None)

        config.DATABASE_PATH = self.tmp_dir / "agent.db"
        config.CLAUDE_CACHE_PATH = self.tmp_dir / "claude_cache.json"
        config.AI_REPORT_PATH = self.tmp_dir / "ai_usage.json"
        config.OBSERVABILITY_LOG_PATH = self.tmp_dir / "events.jsonl"

        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions(ai_enabled=True, max_ai_calls=10))

    def tearDown(self):
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.CLAUDE_CACHE_PATH = self.original_cache_path
        config.AI_REPORT_PATH = self.original_report_path
        if self.original_observability_path is not None:
            config.OBSERVABILITY_LOG_PATH = self.original_observability_path
        shutil.rmtree(self.tmp_dir)

    def _insert_case(
        self,
        case_id,
        *,
        case_type=CASE_TYPE_DATA_ABSENCE,
        building="123 Example Road",
        contractor="Example Elevator",
        device="ELV-1",
        field_value="field value",
    ):
        db.insert_email(
            email_id=f"email-{case_id}",
            message_id=f"message-{case_id}",
            thread_id=None,
            subject="KPI alert",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body=f"raw body must not appear {case_id}",
            normalized_text=f"normalized text must not appear {case_id}",
        )
        if case_type == CASE_TYPE_UNKNOWN:
            db._execute_write(
                """
                INSERT INTO cases
                    (case_id, case_type, status, priority, grouping_key,
                     building, device, contractor, due_date, period, created_at, updated_at)
                VALUES (?, ?, 'open', 'medium', ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    case_id,
                    case_type,
                    f"group-{case_id}",
                    building,
                    device,
                    contractor,
                    "2026-05-01T10:00:00",
                    "2026-05-01T10:00:00",
                ),
            )
        else:
            db.insert_case(
                case_id=case_id,
                case_type=case_type,
                grouping_key=f"group-{case_id}",
                building=building,
                device=device,
                contractor=contractor,
                due_date=None,
                period=None,
            )
        db.insert_extracted_field(
            field_id=f"field-{case_id}",
            case_id=case_id,
            email_id=f"email-{case_id}",
            field_name="safe_note",
            field_value=field_value,
            confidence_score=1.0,
        )
        return case_id

    def _insert_group(self, group_id="group-1"):
        now = "2026-05-01T10:00:00"
        db._execute_write(
            """
            INSERT INTO building_issue_groups
                (group_id, grouping_key, building, normalized_building,
                 contractor, normalized_contractor, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                group_id,
                "building_group::123-example::example-elevator",
                "123 Example Road",
                "123 example road",
                "Example Elevator",
                "example elevator",
                now,
                now,
            ),
        )
        return group_id

    def _link_group_case(self, group_id, case_id):
        db._execute_write(
            """
            INSERT INTO building_issue_group_cases
                (group_id, case_id, added_at, status)
            VALUES (?, ?, ?, 'active')
            """,
            (group_id, case_id, "2026-05-01T10:00:00"),
        )

    def _configure_gateway(self, transport, max_calls=10):
        gateway = get_ai_gateway()
        gateway.reset()
        gateway.configure(
            AiUsageConfig(
                enabled=True,
                max_calls=max_calls,
                budget_mode="manual_review",
                model_name="mock-model",
                cache_enabled=False,
                config_version="test-discovery-packets",
            )
        )
        gateway.set_test_transports(json_transport=transport, transport_mode="mocked")
        return gateway

    def test_pattern_packets_exclude_unsupported_data(self):
        import discovery_packets

        supported_id = self._insert_case("supported-1")
        unsupported_id = self._insert_case("unsupported-1", case_type=CASE_TYPE_UNKNOWN)
        evidence = {
            "supporting_case_ids": [supported_id, unsupported_id],
            "related_case_ids": [supported_id, unsupported_id],
        }
        db.upsert_pattern_flag_record(
            case_id=supported_id,
            pattern_type="repeated_data_absence",
            severity="medium",
            summary="Repeated supported data absence.",
            evidence_json=json.dumps(evidence),
        )
        db.upsert_pattern_flag_record(
            case_id=unsupported_id,
            pattern_type="repeated_data_absence",
            severity="medium",
            summary="Unsupported pattern should not include the unsupported case.",
            evidence_json=json.dumps(evidence),
        )

        packets = discovery_packets.build_pattern_packets("run-patterns")

        self.assertGreaterEqual(len(packets), 1)
        self.assertTrue(all(p["unsupported_records_included"] == 0 for p in packets))
        payload = json.dumps(packets)
        self.assertIn(supported_id, payload)
        self.assertNotIn(unsupported_id, payload)
        self.assertNotIn("raw body must not appear", payload)
        self.assertNotIn("normalized text must not appear", payload)

    def test_building_group_packets_include_supported_child_cases_only(self):
        import discovery_packets

        supported_id = self._insert_case("supported-group-1")
        unsupported_id = self._insert_case("unsupported-group-1", case_type=CASE_TYPE_UNKNOWN)
        group_id = self._insert_group()
        self._link_group_case(group_id, supported_id)
        self._link_group_case(group_id, unsupported_id)

        packets = discovery_packets.build_building_group_packets(group_id)

        payload = json.dumps(packets)
        self.assertIn(supported_id, payload)
        self.assertNotIn(unsupported_id, payload)
        self.assertTrue(all(p["unsupported_records_included"] == 0 for p in packets))
        self.assertTrue(all(p["scope"]["supported_case_types_only"] for p in packets))

    def test_all_supported_packets_split_when_data_exceeds_batch_size(self):
        import discovery_packets

        for idx in range(5):
            self._insert_case(f"batch-{idx}", case_type=CASE_TYPE_DATA_ABSENCE)

        packets = discovery_packets.build_all_supported_packets(
            "run-all",
            packet_by="case-type",
            batch_size=2,
        )

        self.assertGreaterEqual(len(packets), 3)
        self.assertTrue(all(len(packet["cases"]) <= 2 for packet in packets))
        self.assertEqual(5, sum(len(packet["cases"]) for packet in packets))

    def test_oversized_prompt_is_split_or_skipped_with_logged_reason(self):
        import discovery_packets

        for idx in range(4):
            self._insert_case(
                f"oversized-{idx}",
                field_value="x" * 700,
            )

        packets = discovery_packets.build_all_supported_packets(
            "run-oversized",
            packet_by="building",
            batch_size=4,
            max_prompt_chars=1400,
        )

        event_log = config.OBSERVABILITY_LOG_PATH.read_text(encoding="utf-8") if config.OBSERVABILITY_LOG_PATH.exists() else ""
        self.assertTrue(len(packets) > 1 or "packet_skipped" in event_log)
        self.assertTrue(all(len(json.dumps(packet, sort_keys=True)) <= 1400 for packet in packets))

    def test_discovery_run_tracking_records_counts(self):
        import connection_discovery

        case_id = self._insert_case("track-1")
        db.upsert_pattern_flag_record(
            case_id=case_id,
            pattern_type="repeated_data_absence",
            severity="medium",
            summary="Repeated supported data absence.",
            evidence_json=json.dumps({"supporting_case_ids": [case_id]}),
        )
        self._configure_gateway(lambda prompt, model: _valid_hypothesis([case_id]))

        result = connection_discovery.run_discovery(
            max_ai_calls=5,
            scope="patterns",
            dry_run=False,
        )

        self.assertEqual(1, result["hypotheses_proposed"])
        run = db.get_discovery_runs(limit=1)[0]
        self.assertEqual("patterns", run["scope"])
        self.assertEqual("completed", run["status"])
        self.assertEqual(1, run["packets_created"])
        self.assertEqual(1, run["packets_analyzed"])
        self.assertEqual(1, run["ai_calls_used"])
        self.assertEqual(1, run["hypotheses_created"])
        self.assertEqual(0, run["unsupported_records_included"])

    def test_invalid_packet_hypotheses_are_rejected(self):
        import connection_discovery

        case_id = self._insert_case("invalid-1")
        db.upsert_pattern_flag_record(
            case_id=case_id,
            pattern_type="repeated_data_absence",
            severity="medium",
            summary="Repeated supported data absence.",
            evidence_json=json.dumps({"supporting_case_ids": [case_id]}),
        )
        self._configure_gateway(
            lambda prompt, model: {
                "hypotheses": [
                    {
                        "hypothesis_type": "missing_case",
                        "summary": "Possible connection.",
                        "confidence": "medium",
                        "risk_level": "review",
                        "evidence": {"case_ids": ["missing-case"], "description": "bad id"},
                        "reasoning": "Evidence indicates a possible link.",
                        "recommended_human_review": "Review the referenced cases.",
                    },
                    {
                        "hypothesis_type": "bad_confidence",
                        "summary": "Possible connection.",
                        "confidence": "certain",
                        "risk_level": "review",
                        "evidence": {"case_ids": [case_id], "description": "bad confidence"},
                        "reasoning": "Evidence indicates a possible link.",
                        "recommended_human_review": "Review the referenced cases.",
                    },
                ]
            }
        )

        result = connection_discovery.run_discovery(
            max_ai_calls=5,
            scope="patterns",
        )

        self.assertEqual(0, result["hypotheses_proposed"])
        self.assertEqual(2, result["hypotheses_rejected"])
        self.assertEqual(0, len(db.get_connection_hypotheses()))

    def test_duplicate_hypotheses_grouped_by_deterministic_key(self):
        import connection_discovery

        case_id = self._insert_case("dupe-1", case_type=CASE_TYPE_CAT1_COMPLIANCE)
        evidence = {
            "case_ids": [case_id],
            "building": "123 Example Road",
            "contractor": "Example Elevator",
            "device": "ELV-1",
        }
        for idx, confidence in enumerate(("low", "high")):
            hyp_id = f"hyp-dupe-{idx}"
            db.insert_connection_hypothesis(
                hypothesis_id=hyp_id,
                hypothesis_type="device_recurrence",
                summary=f"Possible duplicate {idx}.",
                confidence=confidence,
                risk_level="review",
                evidence_json=json.dumps(evidence),
                reasoning="Evidence indicates a possible duplicate.",
                recommended_human_review="Review the duplicate evidence.",
            )
            db.insert_connection_hypothesis_case(hyp_id, case_id)

        dry_result = connection_discovery.merge_duplicate_hypotheses(dry_run=True)
        self.assertEqual(1, dry_result["duplicate_groups"])
        self.assertEqual(0, dry_result["hypotheses_marked_merged"])

        result = connection_discovery.merge_duplicate_hypotheses(dry_run=False)
        self.assertEqual(1, result["duplicate_groups"])
        self.assertEqual(1, result["hypotheses_marked_merged"])
        statuses = {
            row["hypothesis_id"]: row["status"]
            for row in db.get_connection_hypotheses()
        }
        self.assertEqual("proposed", statuses["hyp-dupe-1"])
        self.assertEqual("merged", statuses["hyp-dupe-0"])

    def test_ai_budget_enforced_for_packetized_discovery(self):
        import connection_discovery

        for idx in range(3):
            case_id = self._insert_case(f"budget-{idx}", building=f"{idx} Example Road")
            db.upsert_pattern_flag_record(
                case_id=case_id,
                pattern_type=f"pattern_{idx}",
                severity="medium",
                summary=f"Pattern {idx}.",
                evidence_json=json.dumps({"supporting_case_ids": [case_id]}),
            )

        calls = []
        self._configure_gateway(lambda prompt, model: calls.append(prompt) or {"hypotheses": []}, max_calls=1)

        result = connection_discovery.run_discovery(
            max_ai_calls=1,
            scope="patterns",
        )

        self.assertEqual(1, len(calls))
        self.assertEqual(1, result["ai_calls_used"])
        self.assertEqual(1, result["packets_analyzed"])
        self.assertGreaterEqual(result["packets_created"], 3)


if __name__ == "__main__":
    unittest.main()
