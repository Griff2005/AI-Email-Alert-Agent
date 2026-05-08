"""
runtime_options.py - Mutable run-level behavior switches for the agent.

The project already uses a static environment-backed config object for durable
settings. This module layers run-scoped safety defaults on top so CLI commands
and the demo harness can enable or disable expensive behaviors without
rewriting the core pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from config import PROJECT_ROOT


@dataclass(frozen=True)
class RuntimeOptions:
    """Safety-oriented runtime switches shared across the process."""

    ai_enabled: bool = False
    allow_uncapped_ai: bool = False
    max_ai_calls: Optional[int] = 0
    max_ai_calls_per_email: int = 0
    max_ai_calls_per_case: int = 0
    max_ai_calls_by_purpose: Dict[str, int] = field(default_factory=dict)
    ai_budget_mode: str = "manual_review"
    ai_report_path: Optional[Path] = PROJECT_ROOT / "data" / "ai_usage_report.json"
    disable_outbound_generation: bool = False
    template_outbound_only: bool = True
    ai_outbound_enabled: bool = False
    followups_enabled: bool = True
    max_followups: int = 3
    max_followup_runs: int = 1000


class _RuntimeOptionsStore:
    """Process-wide mutable runtime options store."""

    def __init__(self) -> None:
        self._options = RuntimeOptions()

    def configure(self, options: RuntimeOptions) -> None:
        self._options = options

    def get(self) -> RuntimeOptions:
        return self._options

    def reset(self) -> None:
        self._options = RuntimeOptions()


runtime_options = _RuntimeOptionsStore()
