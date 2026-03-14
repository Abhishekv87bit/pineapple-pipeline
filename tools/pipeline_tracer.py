"""
Pipeline Tracer -- structured JSONL tracing for pipeline stage transitions.

Not full OpenTelemetry (overkill for single-machine), but structured enough
to query with jq:

    jq '.event' .pineapple/runs/<uuid>/trace.jsonl
    jq 'select(.event == "stage_transition")' trace.jsonl
    jq 'select(.duration_ms > 1000)' trace.jsonl
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("pineapple.tracer")


class PipelineTracer:
    """Append-only JSONL tracer for a single pipeline run."""

    def __init__(self, project_path: Path, run_id: str):
        self.project_path = project_path
        self.run_id = run_id
        self.trace_file = project_path / ".pineapple" / "runs" / run_id / "trace.jsonl"
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, entry: dict):
        """Append a single JSON line to the trace file."""
        entry["run_id"] = self.run_id
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        line = json.dumps(entry, default=str)
        with open(self.trace_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log_stage_transition(
        self,
        from_stage: str,
        to_stage: str,
        reason: str = "",
        duration_ms: float = 0,
        metadata: dict[str, Any] | None = None,
    ):
        """Log a stage transition."""
        self._append({
            "event": "stage_transition",
            "from_stage": from_stage,
            "to_stage": to_stage,
            "reason": reason,
            "duration_ms": round(duration_ms, 1),
            "metadata": metadata or {},
        })

    def log_agent_dispatch(
        self,
        stage: str,
        agent_type: str,
        task: str,
        metadata: dict[str, Any] | None = None,
    ):
        """Log an agent dispatch event."""
        self._append({
            "event": "agent_dispatch",
            "stage": stage,
            "agent_type": agent_type,
            "task": task,
            "metadata": metadata or {},
        })

    def log_agent_result(
        self,
        stage: str,
        agent_type: str,
        success: bool,
        duration_ms: float = 0,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Log an agent result."""
        self._append({
            "event": "agent_result",
            "stage": stage,
            "agent_type": agent_type,
            "success": success,
            "duration_ms": round(duration_ms, 1),
            "error": error,
            "metadata": metadata or {},
        })

    def log_verification(
        self,
        layers_passed: list[int],
        layers_failed: list[int],
        test_count: int,
        all_green: bool,
        duration_ms: float = 0,
    ):
        """Log a verification run."""
        self._append({
            "event": "verification",
            "layers_passed": layers_passed,
            "layers_failed": layers_failed,
            "test_count": test_count,
            "all_green": all_green,
            "duration_ms": round(duration_ms, 1),
        })

    def log_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        call_name: str = "",
    ):
        """Log an LLM cost event."""
        self._append({
            "event": "llm_cost",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "call_name": call_name,
        })

    def log_error(
        self,
        stage: str,
        error: str,
        recoverable: bool = True,
        metadata: dict[str, Any] | None = None,
    ):
        """Log an error event."""
        self._append({
            "event": "error",
            "stage": stage,
            "error": error,
            "recoverable": recoverable,
            "metadata": metadata or {},
        })

    def log_custom(self, event: str, **kwargs):
        """Log any custom event."""
        self._append({"event": event, **kwargs})

    @contextmanager
    def trace_stage(self, from_stage: str, to_stage: str, reason: str = ""):
        """Context manager that traces a stage transition with timing."""
        start = time.time()
        try:
            yield
        except Exception as e:
            duration = (time.time() - start) * 1000
            self.log_error(from_stage, str(e), recoverable=False)
            raise
        else:
            duration = (time.time() - start) * 1000
            self.log_stage_transition(from_stage, to_stage, reason, duration)

    def get_trace(self) -> list[dict]:
        """Read all trace entries."""
        if not self.trace_file.is_file():
            return []
        entries = []
        for line in self.trace_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def get_summary(self) -> dict:
        """Get a summary of the trace."""
        entries = self.get_trace()
        if not entries:
            return {"total_events": 0}

        transitions = [e for e in entries if e.get("event") == "stage_transition"]
        costs = [e for e in entries if e.get("event") == "llm_cost"]
        errors = [e for e in entries if e.get("event") == "error"]

        total_cost = sum(e.get("cost_usd", 0) for e in costs)
        total_tokens = sum(e.get("input_tokens", 0) + e.get("output_tokens", 0) for e in costs)

        return {
            "total_events": len(entries),
            "stage_transitions": len(transitions),
            "llm_calls": len(costs),
            "total_cost_usd": round(total_cost, 4),
            "total_tokens": total_tokens,
            "errors": len(errors),
            "first_event": entries[0].get("timestamp", "") if entries else "",
            "last_event": entries[-1].get("timestamp", "") if entries else "",
        }
