"""OpenAI Codex provider for AI Radar.

Codex already stores its login under ~/.codex and writes the rate-limit state
returned by OpenAI into local session transcripts.  Reading that state avoids
asking for credentials or copying OAuth tokens into AI Radar.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from providers import MetricData, ProviderStatus

logger = logging.getLogger(__name__)

PROVIDER_KEY = "codex"
DISPLAY_NAME = "CDX"
FULL_NAME = "OpenAI Codex"

CODEX_DIR = Path.home() / ".codex"
AUTH_FILE = CODEX_DIR / "auth.json"
SESSIONS_DIR = CODEX_DIR / "sessions"

_THRESHOLDS = {"yellow": 80, "orange": 90, "red": 100}


def _set_thresholds(config: dict) -> None:
    global _THRESHOLDS
    values = config.get("thresholds", {})
    _THRESHOLDS = {
        "yellow": values.get("yellow", 80),
        "orange": values.get("orange", 90),
        "red": values.get("red", 100),
    }


def _color(pct: float) -> str:
    if pct >= _THRESHOLDS["red"]:
        return "red"
    if pct >= _THRESHOLDS["orange"]:
        return "orange"
    if pct >= _THRESHOLDS["yellow"]:
        return "yellow"
    return "green"


def _is_logged_in() -> bool:
    """Check Codex's credential file without returning or logging secrets."""
    try:
        auth = json.loads(AUTH_FILE.read_text())
        if auth.get("OPENAI_API_KEY"):
            return True
        tokens = auth.get("tokens") or {}
        return bool(tokens.get("access_token"))
    except (OSError, ValueError, TypeError):
        return False


def _latest_rate_limits() -> dict:
    """Read the newest rate-limit snapshot emitted by Codex."""
    if not SESSIONS_DIR.exists():
        return {}

    try:
        paths = sorted(
            SESSIONS_DIR.rglob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return {}

    # A recent session normally contains many snapshots. Checking a bounded
    # number of files keeps a SwiftBar refresh fast even for long-time users.
    for path in paths[:25]:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line or '"token_count"' not in line:
                continue
            try:
                event = json.loads(line)
                payload = event.get("payload") or {}
                if payload.get("type") != "token_count":
                    continue
                limits = payload.get("rate_limits") or {}
                if limits:
                    return limits
            except (ValueError, TypeError):
                continue
    return {}


def _duration(minutes: int | float) -> str:
    minutes = int(minutes or 0)
    if minutes and minutes % 10080 == 0:
        weeks = minutes // 10080
        return "Weekly" if weeks == 1 else f"{weeks}-week"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m" if minutes else "Usage"


def _reset_label(epoch: int | float | None) -> str:
    if not epoch:
        return ""
    try:
        reset = datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone()
        now = datetime.now().astimezone()
        seconds = max(0, int((reset - now).total_seconds()))
        minutes = seconds // 60
        if minutes < 60:
            relative = f"{minutes}m"
        elif minutes < 1440:
            relative = f"{minutes // 60}h {minutes % 60}m"
        else:
            relative = f"{minutes // 1440}d {(minutes % 1440) // 60}h"
        absolute = reset.strftime("%b %-d %-I:%M %p")
        return f"{relative} ({absolute})"
    except (ValueError, TypeError, OSError, OverflowError):
        return ""


def _forecast_pct(pct: float, window: dict) -> float:
    """Project current utilization linearly to the end of its window."""
    try:
        reset = float(window["resets_at"])
        duration = float(window["window_minutes"]) * 60
        elapsed = duration - (reset - datetime.now(tz=timezone.utc).timestamp())
        if elapsed <= 0 or duration <= 0:
            return pct
        return pct / elapsed * duration
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        return pct


def _metric(window: dict, position: str) -> MetricData | None:
    if not isinstance(window, dict) or window.get("used_percent") is None:
        return None
    pct = float(window.get("used_percent") or 0)
    forecast = _forecast_pct(pct, window)
    minutes = int(window.get("window_minutes") or 0)
    duration = _duration(minutes)
    return MetricData(
        label=f"{duration} limit",
        short_label=duration.replace("Weekly", "7d"),
        pct=pct,
        forecast_pct=forecast,
        color=_color(forecast),
        reset_label=_reset_label(window.get("resets_at")),
        extra=f"{100 - pct:.0f}% remaining · {position} window",
    )


def fetch_status(config: dict, global_config: dict | None = None) -> ProviderStatus:
    """Return usage from the user's existing Codex login and local state."""
    if global_config:
        _set_thresholds(global_config)

    if not _is_logged_in():
        return ProviderStatus(
            name="OpenAI Codex",
            short_name=DISPLAY_NAME,
            summary="--",
            error="Not logged in to Codex. Run `codex login`.",
            auth_cli="codex",
        )

    limits = _latest_rate_limits()
    if not limits:
        return ProviderStatus(
            name="OpenAI Codex",
            short_name=DISPLAY_NAME,
            summary="--",
            plan_label="Signed in",
            error="No usage snapshot yet. Start a Codex session, then refresh.",
            auth_cli="codex",
        )

    metrics = [
        metric
        for metric in (
            _metric(limits.get("primary"), "primary"),
            _metric(limits.get("secondary"), "secondary"),
        )
        if metric is not None
    ]
    credits = limits.get("credits") or {}
    if credits.get("has_credits"):
        balance = credits.get("balance")
        label = "Unlimited" if credits.get("unlimited") else str(balance or "Available")
        metrics.append(
            MetricData(
                label="Credits",
                short_label="$",
                pct=0,
                forecast_pct=0,
                color="green",
                reset_label="",
                extra=label,
                detail_only=True,
                status_only=True,
            )
        )

    worst = max((m.forecast_pct for m in metrics if not m.status_only), default=0)
    plan = str(limits.get("plan_type") or "").replace("_", " ").title()
    summary = f"{metrics[0].pct:.0f}%" if metrics else "Active"
    breakdown: dict[str, list[tuple[str, str]]] = {}
    if config.get("usage_breakdown", True):
        try:
            from providers.codex_usage_breakdown import compute_breakdown

            breakdown = compute_breakdown()
        except Exception as exc:
            logger.warning("Codex usage breakdown failed: %s", exc)
    return ProviderStatus(
        name="OpenAI Codex",
        short_name=DISPLAY_NAME,
        summary=summary,
        color="white" if _color(worst) == "green" else _color(worst),
        metrics=metrics,
        plan_label=plan,
        breakdown=breakdown,
        auth_cli="codex",
    )
