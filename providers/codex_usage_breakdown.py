"""Approximate local Codex usage analytics from ~/.codex/sessions.

The server-provided percentage remains the source of truth for limits. This
module only explains activity recorded on this Mac and never reads auth.json.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_DIR = Path.home() / ".codex" / "sessions"
HIGH_CONTEXT_TOKENS = 150_000


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _fmt_tokens(value: int | float) -> str:
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _session_summary(path: Path) -> dict | None:
    """Return the final cumulative counters and peak request for one session."""
    latest_usage: dict = {}
    latest_time: datetime | None = None
    peak_context = 0
    model = ""
    is_subagent = False

    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None

    for line in lines:
        if '"token_count"' not in line and '"session_meta"' not in line and '"turn_context"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        payload = event.get("payload") or {}
        event_type = event.get("type")

        if event_type == "session_meta":
            source = str(payload.get("thread_source") or "").lower()
            role = str(payload.get("agent_role") or "").lower()
            is_subagent = source in {"subagent", "sub_agent"} or bool(role)
        elif event_type == "turn_context":
            model = str(payload.get("model") or model)

        if payload.get("type") != "token_count":
            continue
        info = payload.get("info") or {}
        usage = info.get("total_token_usage") or {}
        if usage:
            latest_usage = usage
            latest_time = _parse_time(event.get("timestamp", "")) or latest_time
        last = info.get("last_token_usage") or {}
        # Codex input_tokens already includes its cached_input_tokens subset.
        context = last.get("input_tokens") or 0
        peak_context = max(peak_context, int(context))

    if not latest_usage or latest_time is None:
        return None
    return {
        "time": latest_time,
        "usage": latest_usage,
        "peak_context": peak_context,
        "model": model,
        "subagent": is_subagent,
    }


def _insights(sessions: list[dict]) -> list[tuple[str, str]]:
    if not sessions:
        return []

    input_tokens = sum(int(s["usage"].get("input_tokens") or 0) for s in sessions)
    cached_tokens = sum(int(s["usage"].get("cached_input_tokens") or 0) for s in sessions)
    output_tokens = sum(int(s["usage"].get("output_tokens") or 0) for s in sessions)
    reasoning_tokens = sum(int(s["usage"].get("reasoning_output_tokens") or 0) for s in sessions)
    total_tokens = sum(int(s["usage"].get("total_tokens") or 0) for s in sessions)

    out = [
        (
            f"{_fmt_tokens(total_tokens)} tokens · {len(sessions)} session{'s' if len(sessions) != 1 else ''}",
            f"{_fmt_tokens(input_tokens)} input · {_fmt_tokens(output_tokens)} output · "
            f"{_fmt_tokens(reasoning_tokens)} reasoning",
        )
    ]

    if input_tokens > 0 and cached_tokens > 0:
        cached_pct = round(cached_tokens / input_tokens * 100)
        out.append(
            (
                f"{cached_pct}% of input served from cache",
                f"{_fmt_tokens(cached_tokens)} cached input tokens reused.",
            )
        )

    # Weight uncached input, cached input, and output differently to approximate
    # which sessions are most likely driving a plan limit.
    def weight(session: dict) -> float:
        usage = session["usage"]
        inp = int(usage.get("input_tokens") or 0)
        cached = int(usage.get("cached_input_tokens") or 0)
        output = int(usage.get("output_tokens") or 0)
        return max(inp - cached, 0) + cached * 0.1 + output * 5

    weighted_total = sum(weight(s) for s in sessions)
    if weighted_total > 0:
        high = sum(weight(s) for s in sessions if s["peak_context"] > HIGH_CONTEXT_TOKENS)
        if high > 0:
            pct = round(high / weighted_total * 100)
            out.append(
                (
                    f"{pct}% from >150k context sessions",
                    "Large requests consume more even when some input is cached.",
                )
            )
        subagents = sum(weight(s) for s in sessions if s["subagent"])
        if subagents > 0:
            pct = round(subagents / weighted_total * 100)
            out.append(
                (f"{pct}% from subagents", "Each subagent has its own model requests.")
            )

    models: Counter[str] = Counter()
    for session in sessions:
        if session["model"]:
            models[session["model"]] += int(session["usage"].get("total_tokens") or 0)
    if models:
        model_total = sum(models.values())
        for model, tokens in models.most_common(3):
            pct = round(tokens / model_total * 100) if model_total else 0
            out.append(
                (f"{model}: {_fmt_tokens(tokens)} tokens", f"{pct}% of local model tokens.")
            )

    return out


def compute_breakdown(now: datetime | None = None) -> dict[str, list[tuple[str, str]]]:
    """Return Day/Week analytics using the final counters of local sessions."""
    if not SESSIONS_DIR.exists():
        return {"day": [], "week": []}
    now = now or datetime.now(timezone.utc)
    day: list[dict] = []
    week: list[dict] = []

    try:
        paths = list(SESSIONS_DIR.rglob("*.jsonl"))
    except OSError:
        return {"day": [], "week": []}

    for path in paths:
        summary = _session_summary(path)
        if not summary:
            continue
        age_hours = (now - summary["time"]).total_seconds() / 3600
        if 0 <= age_hours <= 168:
            week.append(summary)
            if age_hours <= 24:
                day.append(summary)

    return {"day": _insights(day), "week": _insights(week)}
