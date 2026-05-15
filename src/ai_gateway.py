"""
ai_gateway.py - Centralized budgeted gateway for all model usage.

All live or mocked model calls must flow through this module. It enforces
per-run budgets, performs stable cache lookups, records detailed usage
telemetry, and produces deterministic reports for the demo harness and normal
agent commands.
"""

from __future__ import annotations

import atexit
import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import claude_client
from config import config
from time_utils import utc_now_iso

AI_PURPOSES = {
    "classification",
    "extraction",
    "outbound_draft_generation",
    "followup_generation",
    "reply_analysis",
    "pattern_analysis",
    "manual_review_reasoning",
    "connection_discovery",
    "other",
}

_CACHE_SCHEMA_VERSION = "ai-gateway-v1"


@dataclass(frozen=True)
class AiUsageConfig:
    """Runtime policy for AI calls made through ``AiGateway``."""

    enabled: bool = False
    allow_uncapped_ai: bool = False
    max_calls: Optional[int] = 0
    max_calls_per_email: int = 0
    max_calls_per_case: int = 0
    max_calls_by_purpose: Dict[str, int] = field(default_factory=dict)
    budget_mode: str = "manual_review"
    report_path: Optional[Path] = field(default_factory=lambda: getattr(config, "AI_REPORT_PATH", None))
    csv_report_path: Optional[Path] = None
    model_name: str = field(default_factory=lambda: config.CLAUDE_MODEL)
    cache_enabled: bool = True
    cache_path: Path = field(default_factory=lambda: config.CLAUDE_CACHE_PATH)
    config_version: str = "default"


@dataclass
class AiCallOutcome:
    """Result returned by an attempted AI call."""

    status: str
    payload: Optional[Any]
    reason: str
    from_cache: bool = False
    live_call: bool = False
    mocked_call: bool = False


@dataclass
class _AiCallRecord:
    timestamp: str
    purpose: str
    prompt_type: str
    caller: str
    model_name: str
    status: str
    reason: str
    email_id: Optional[str]
    case_id: Optional[str]
    case_type: Optional[str]
    cache_hit: bool
    estimated_input_tokens: int
    estimated_output_tokens: int
    live_call: bool
    mocked_call: bool


class AiBudgetExceeded(RuntimeError):
    """Raised when the gateway is configured to fail fast on budget overflow."""


class AiGateway:
    """Centralized controller for all model requests.

    Enforces per-run ``AiUsageConfig`` budgets (global, per-email, per-case,
    and per-purpose call caps). Maintains a JSON prompt cache keyed by
    content hash so identical prompts are never charged against the live
    budget twice. Records per-call telemetry (purpose, status, token
    estimates, cache hit) and exposes ``build_report()`` for usage summaries.

    In tests, install deterministic replacements for the live Claude transport
    via ``set_test_transports(json_transport=..., text_transport=...)``.
    Use ``gateway.reset()`` in ``setUp``/``tearDown`` to prevent state leakage
    between test cases.

    All product modules must access this singleton through
    ``ai_gateway.get_ai_gateway()`` — never import ``claude_client`` directly.
    """

    def __init__(self) -> None:
        self._config = AiUsageConfig()
        self._records: List[_AiCallRecord] = []
        # Running counter for total_ai_calls — avoids O(N) scan of _records on
        # every _allow_call() check (which would make budget enforcement O(N²)
        # for long discovery runs).  Mirrors build_report()["total_ai_calls"]
        # exactly: incremented after every "allowed" or "mocked" record is
        # appended, never for "blocked" or "cached" records.
        self._ai_call_count: int = 0
        self._cache: Optional[Dict[str, Any]] = None
        self._json_transport: Optional[Callable[[str, str], Dict[str, Any]]] = None
        self._text_transport: Optional[Callable[[str, str], str]] = None
        self._transport_mode = "live"
        self._run_metadata: Dict[str, Any] = {}
        self._atexit_registered = False

    def configure(self, usage_config: AiUsageConfig) -> None:
        """Apply a run-level AI usage policy."""
        if usage_config.enabled and usage_config.max_calls in (None, 0) and not usage_config.allow_uncapped_ai:
            raise ValueError(
                "AI is enabled without a cap. Set max_calls or explicitly allow uncapped AI."
            )
        self._config = usage_config
        self._ensure_atexit_handler()

    def reset(self) -> None:
        """Reset usage records, transports, cache, and runtime policy."""
        self._config = AiUsageConfig()
        self._records = []
        self._ai_call_count = 0
        self._cache = None
        self._json_transport = None
        self._text_transport = None
        self._transport_mode = "live"
        self._run_metadata = {}

    def set_test_transports(
        self,
        json_transport: Optional[Callable[[str, str], Dict[str, Any]]] = None,
        text_transport: Optional[Callable[[str, str], str]] = None,
        transport_mode: str = "allowed",
    ) -> None:
        """Install deterministic test transports in place of live Claude calls."""
        self._json_transport = json_transport
        self._text_transport = text_transport
        self._transport_mode = transport_mode

    def clear_test_transports(self) -> None:
        """Remove deterministic transports and restore live transport mode."""
        self._json_transport = None
        self._text_transport = None
        self._transport_mode = "live"

    def set_run_metadata(self, **kwargs: Any) -> None:
        """Attach arbitrary report metadata for the current command run."""
        self._run_metadata.update(kwargs)

    def record_skip(
        self,
        purpose: str,
        prompt_type: str,
        caller: str,
        reason: str,
        email_id: Optional[str] = None,
        case_id: Optional[str] = None,
        case_type: Optional[str] = None,
    ) -> None:
        """Record that deterministic logic skipped a model call."""
        self._records.append(
            _AiCallRecord(
                timestamp=utc_now_iso(),
                purpose=self._normalize_purpose(purpose),
                prompt_type=prompt_type,
                caller=caller,
                model_name=self._config.model_name,
                status="skipped",
                reason=reason,
                email_id=email_id,
                case_id=case_id,
                case_type=case_type,
                cache_hit=False,
                estimated_input_tokens=0,
                estimated_output_tokens=0,
                live_call=False,
                mocked_call=False,
            )
        )

    def call_json(
        self,
        prompt: str,
        purpose: str,
        prompt_type: str,
        caller: str,
        email_id: Optional[str] = None,
        case_id: Optional[str] = None,
        case_type: Optional[str] = None,
        use_cache: bool = True,
        schema_version: str = "default",
    ) -> AiCallOutcome:
        return self._call(
            prompt=prompt,
            purpose=purpose,
            prompt_type=prompt_type,
            caller=caller,
            email_id=email_id,
            case_id=case_id,
            case_type=case_type,
            use_cache=use_cache,
            schema_version=schema_version,
            expect_json=True,
        )

    def call_text(
        self,
        prompt: str,
        purpose: str,
        prompt_type: str,
        caller: str,
        email_id: Optional[str] = None,
        case_id: Optional[str] = None,
        case_type: Optional[str] = None,
        use_cache: bool = True,
        schema_version: str = "default",
    ) -> AiCallOutcome:
        return self._call(
            prompt=prompt,
            purpose=purpose,
            prompt_type=prompt_type,
            caller=caller,
            email_id=email_id,
            case_id=case_id,
            case_type=case_type,
            use_cache=use_cache,
            schema_version=schema_version,
            expect_json=False,
        )

    def build_report(self) -> Dict[str, Any]:
        """Build a JSON-serializable AI usage audit report."""
        by_purpose_calls = Counter()
        by_case_type = Counter()
        by_component = Counter()
        status_counter = Counter()
        total_input_tokens = 0
        total_output_tokens = 0
        cache_hits = 0
        cache_misses = 0
        total_ai_calls = 0
        live_ai_calls = 0
        mocked_ai_calls = 0

        for record in self._records:
            status_counter[record.status] += 1
            if record.status in {"allowed", "mocked"}:
                by_purpose_calls[record.purpose] += 1
                by_component[record.caller] += 1
                if record.case_type:
                    by_case_type[record.case_type] += 1
                total_ai_calls += 1
                total_input_tokens += record.estimated_input_tokens
                total_output_tokens += record.estimated_output_tokens
                if record.live_call:
                    live_ai_calls += 1
                if record.mocked_call:
                    mocked_ai_calls += 1
            if record.status == "cached":
                cache_hits += 1
            if record.status in {"allowed", "mocked"} and not record.cache_hit:
                cache_misses += 1

        attempted = cache_hits + cache_misses
        return {
            "ai_enabled": self._config.enabled,
            "allow_uncapped_ai": self._config.allow_uncapped_ai,
            "budget_mode": self._config.budget_mode,
            "max_budget_configured": {
                "max_calls": self._config.max_calls,
                "max_calls_per_email": self._config.max_calls_per_email,
                "max_calls_per_case": self._config.max_calls_per_case,
                "max_calls_by_purpose": dict(self._config.max_calls_by_purpose),
            },
            "model_name": self._config.model_name,
            "total_ai_calls": total_ai_calls,
            "live_ai_calls": live_ai_calls,
            "mocked_ai_calls": mocked_ai_calls,
            "total_ai_calls_blocked": status_counter["blocked"],
            "total_ai_calls_skipped": status_counter["skipped"],
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "cache_hit_rate": round(cache_hits / attempted, 4) if attempted else 0.0,
            "calls_avoided_by_cache": cache_hits,
            "token_accounting": "estimated",
            "estimated_input_tokens": total_input_tokens,
            "estimated_output_tokens": total_output_tokens,
            "ai_calls_by_purpose": dict(by_purpose_calls),
            "ai_calls_by_case_type": dict(by_case_type),
            "ai_calls_by_component": dict(by_component),
            "status_counts": dict(status_counter),
            "records": [asdict(record) for record in self._records],
            "warnings": self._report_warnings(total_ai_calls, status_counter),
            "run_metadata": dict(self._run_metadata),
        }

    def write_report(
        self,
        report_path: Optional[Path] = None,
        csv_path: Optional[Path] = None,
    ) -> Optional[Path]:
        """Write JSON and optional CSV AI usage reports."""
        report_path = report_path or self._config.report_path
        if report_path is None:
            return None
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = self.build_report()
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        csv_target = csv_path or self._config.csv_report_path
        if csv_target is not None:
            csv_target.parent.mkdir(parents=True, exist_ok=True)
            self._write_csv(csv_target)
        return report_path

    def _call(
        self,
        prompt: str,
        purpose: str,
        prompt_type: str,
        caller: str,
        email_id: Optional[str],
        case_id: Optional[str],
        case_type: Optional[str],
        use_cache: bool,
        schema_version: str,
        expect_json: bool,
    ) -> AiCallOutcome:
        normalized_purpose = self._normalize_purpose(purpose)
        allowance = self._allow_call(normalized_purpose, email_id, case_id)
        if allowance is not None:
            return self._blocked_outcome(
                reason=allowance,
                purpose=normalized_purpose,
                prompt_type=prompt_type,
                caller=caller,
                email_id=email_id,
                case_id=case_id,
                case_type=case_type,
            )

        cache_key = self._cache_key(
            purpose=normalized_purpose,
            prompt_type=prompt_type,
            prompt=prompt,
            model_name=self._config.model_name,
            schema_version=schema_version,
            config_version=self._config.config_version,
        )
        if use_cache and self._config.cache_enabled:
            cache = self._load_cache()
            if cache_key in cache:
                cached_payload = cache[cache_key]
                self._records.append(
                    _AiCallRecord(
                        timestamp=utc_now_iso(),
                        purpose=normalized_purpose,
                        prompt_type=prompt_type,
                        caller=caller,
                        model_name=self._config.model_name,
                        status="cached",
                        reason="Satisfied from AI cache.",
                        email_id=email_id,
                        case_id=case_id,
                        case_type=case_type,
                        cache_hit=True,
                        estimated_input_tokens=self._estimate_tokens(prompt),
                        estimated_output_tokens=self._estimate_tokens(json.dumps(cached_payload, sort_keys=True)),
                        live_call=False,
                        mocked_call=False,
                    )
                )
                return AiCallOutcome(
                    status="cached",
                    payload=cached_payload,
                    reason="Satisfied from AI cache.",
                    from_cache=True,
                    live_call=False,
                    mocked_call=False,
                )

        if expect_json:
            payload = self._invoke_json_transport(prompt)
        else:
            payload = self._invoke_text_transport(prompt)

        if use_cache and self._config.cache_enabled:
            cache = self._load_cache()
            cache[cache_key] = payload
            self._save_cache()

        mocked_call = self._transport_mode == "mocked"
        status = "mocked" if mocked_call else "allowed"
        output_payload = payload if not expect_json else dict(payload)
        self._records.append(
            _AiCallRecord(
                timestamp=utc_now_iso(),
                purpose=normalized_purpose,
                prompt_type=prompt_type,
                caller=caller,
                model_name=self._config.model_name,
                status=status,
                reason="AI call executed.",
                email_id=email_id,
                case_id=case_id,
                case_type=case_type,
                cache_hit=False,
                estimated_input_tokens=self._estimate_tokens(prompt),
                estimated_output_tokens=self._estimate_tokens(
                    output_payload if isinstance(output_payload, str) else json.dumps(output_payload, sort_keys=True)
                ),
                live_call=not mocked_call,
                mocked_call=mocked_call,
            )
        )
        # Keep the running counter in sync with build_report()["total_ai_calls"].
        # Status is always "allowed" or "mocked" at this point — both count.
        self._ai_call_count += 1
        return AiCallOutcome(
            status=status,
            payload=output_payload,
            reason="AI call executed.",
            from_cache=False,
            live_call=not mocked_call,
            mocked_call=mocked_call,
        )

    def _blocked_outcome(
        self,
        reason: str,
        purpose: str,
        prompt_type: str,
        caller: str,
        email_id: Optional[str],
        case_id: Optional[str],
        case_type: Optional[str],
    ) -> AiCallOutcome:
        self._records.append(
            _AiCallRecord(
                timestamp=utc_now_iso(),
                purpose=purpose,
                prompt_type=prompt_type,
                caller=caller,
                model_name=self._config.model_name,
                status="blocked",
                reason=reason,
                email_id=email_id,
                case_id=case_id,
                case_type=case_type,
                cache_hit=False,
                estimated_input_tokens=0,
                estimated_output_tokens=0,
                live_call=False,
                mocked_call=False,
            )
        )
        if self._config.budget_mode == "fail":
            raise AiBudgetExceeded(reason)
        return AiCallOutcome(
            status="blocked",
            payload=None,
            reason=reason,
            from_cache=False,
            live_call=False,
            mocked_call=False,
        )

    def _allow_call(self, purpose: str, email_id: Optional[str], case_id: Optional[str]) -> Optional[str]:
        """Check whether a new AI call is permitted under the active budget policy.

        Returns ``None`` if the call should proceed, or a non-empty string
        describing the block reason when the call must be suppressed.

        Checks are applied in order: AI enabled, global max_calls,
        per-email cap, per-case cap, and per-purpose cap. The first failing
        check short-circuits and returns its reason string.
        """
        if not self._config.enabled:
            return "AI is disabled for this run."

        # Use the O(1) running counter instead of build_report() (which is O(N)).
        # _ai_call_count mirrors total_ai_calls exactly — both track "allowed"
        # and "mocked" records only.
        if self._config.max_calls is not None and self._ai_call_count >= self._config.max_calls:
            return f"AI budget exceeded: max_calls={self._config.max_calls}"

        if email_id and self._config.max_calls_per_email > 0:
            email_count = sum(
                1
                for record in self._records
                if record.email_id == email_id and record.status in {"allowed", "mocked"}
            )
            if email_count >= self._config.max_calls_per_email:
                return f"AI budget exceeded for email {email_id}: max_calls_per_email={self._config.max_calls_per_email}"

        if case_id and self._config.max_calls_per_case > 0:
            case_count = sum(
                1
                for record in self._records
                if record.case_id == case_id and record.status in {"allowed", "mocked"}
            )
            if case_count >= self._config.max_calls_per_case:
                return f"AI budget exceeded for case {case_id}: max_calls_per_case={self._config.max_calls_per_case}"

        per_purpose_cap = self._config.max_calls_by_purpose.get(purpose, 0)
        if per_purpose_cap > 0:
            purpose_count = sum(
                1
                for record in self._records
                if record.purpose == purpose and record.status in {"allowed", "mocked"}
            )
            if purpose_count >= per_purpose_cap:
                return f"AI budget exceeded for purpose {purpose}: max_calls_by_purpose={per_purpose_cap}"

        return None

    def _invoke_json_transport(self, prompt: str) -> Dict[str, Any]:
        if self._json_transport is not None:
            return dict(self._json_transport(prompt, self._config.model_name))
        self._transport_mode = "live"
        return claude_client.call_claude_json(prompt, use_cache=False)

    def _invoke_text_transport(self, prompt: str) -> str:
        if self._text_transport is not None:
            return str(self._text_transport(prompt, self._config.model_name))
        self._transport_mode = "live"
        return claude_client.call_claude(prompt, use_cache=False)

    def _load_cache(self) -> Dict[str, Any]:
        if self._cache is not None:
            return self._cache
        path = self._config.cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                self._cache = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}
        else:
            self._cache = {}
        return self._cache

    def _save_cache(self) -> None:
        if self._cache is None:
            return
        self._config.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._config.cache_path.write_text(
            json.dumps(self._cache, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _cache_key(
        self,
        purpose: str,
        prompt_type: str,
        prompt: str,
        model_name: str,
        schema_version: str,
        config_version: str,
    ) -> str:
        normalized_prompt = " ".join(prompt.split())
        payload = {
            "cache_schema_version": _CACHE_SCHEMA_VERSION,
            "purpose": purpose,
            "prompt_type": prompt_type,
            "model_name": model_name,
            "schema_version": schema_version,
            "config_version": config_version,
            "normalized_prompt": normalized_prompt,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def _estimate_tokens(value: str) -> int:
        if not value:
            return 0
        return max(1, len(value) // 4)

    @staticmethod
    def _normalize_purpose(value: str) -> str:
        return value if value in AI_PURPOSES else "other"

    @staticmethod
    def _report_warnings(total_ai_calls: int, status_counter: Counter) -> List[str]:
        warnings: List[str] = []
        if total_ai_calls >= 20:
            warnings.append("High AI activity detected for this run.")
        if status_counter["blocked"] > 0:
            warnings.append("Some AI calls were blocked by policy or budget.")
        return warnings

    def _write_csv(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(asdict(self._records[0]).keys()) if self._records else [
                    "timestamp",
                    "purpose",
                    "prompt_type",
                    "caller",
                    "model_name",
                    "status",
                    "reason",
                    "email_id",
                    "case_id",
                    "case_type",
                    "cache_hit",
                    "estimated_input_tokens",
                    "estimated_output_tokens",
                    "live_call",
                    "mocked_call",
                ],
            )
            writer.writeheader()
            for record in self._records:
                writer.writerow(asdict(record))

    def _ensure_atexit_handler(self) -> None:
        if self._atexit_registered:
            return
        atexit.register(self.write_report)
        self._atexit_registered = True


_AI_GATEWAY = AiGateway()


def get_ai_gateway() -> AiGateway:
    return _AI_GATEWAY


def reset_gateway() -> None:
    _AI_GATEWAY.reset()
