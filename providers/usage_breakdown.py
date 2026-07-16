"""Local usage breakdown — approximate "what's driving your usage" analysis.

Mirrors Claude Code's `/usage` "What's contributing to your limits usage?" panel,
computed locally from session transcripts in ~/.claude/projects/. Like that panel,
this is approximate and based only on local sessions on this machine — it does not
include usage from other devices or claude.ai.

The metric is cost-weighted (not raw token counts): output and cache-creation tokens
are far more expensive than cached reads, so percentages weight each message by an
approximate relative cost.
"""

from __future__ import annotations

import glob
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# High-context threshold — messages reading more than this many tokens are
# flagged as expensive-context (matches Claude Code's 150k callout).
HIGH_CONTEXT_TOKENS = 150_000

# A session is "subagent-heavy" when subagents account for at least this share
# of its cost.
SUBAGENT_HEAVY_RATIO = 0.5

# Approximate relative per-token cost weights. Absolute values don't matter —
# only the ratios, since everything is normalized into percentages.
_W_INPUT = 1.0
_W_CACHE_WRITE = 1.25
_W_CACHE_READ = 0.1
_W_OUTPUT = 5.0


def _msg_cost(u: dict) -> float:
    return (
        (u.get("input_tokens") or 0) * _W_INPUT
        + (u.get("cache_creation_input_tokens") or 0) * _W_CACHE_WRITE
        + (u.get("cache_read_input_tokens") or 0) * _W_CACHE_READ
        + (u.get("output_tokens") or 0) * _W_OUTPUT
    )


def _context_tokens(u: dict) -> int:
    return (
        (u.get("input_tokens") or 0)
        + (u.get("cache_read_input_tokens") or 0)
        + (u.get("cache_creation_input_tokens") or 0)
    )


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


class _Accumulator:
    """Cost accumulators for a single time window."""

    def __init__(self) -> None:
        self.total = 0.0
        self.high_context = 0.0
        self.subagent = 0.0
        # per top-level session: [total_cost, subagent_cost]
        self.sessions: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])

    def add(self, session_id: str, cost: float, is_subagent: bool, high_ctx: bool) -> None:
        self.total += cost
        if high_ctx:
            self.high_context += cost
        s = self.sessions[session_id]
        s[0] += cost
        if is_subagent:
            self.subagent += cost
            s[1] += cost

    def insights(self) -> list[tuple[str, str]]:
        """Return [(percent_label, description), ...], most significant first."""
        if self.total <= 0:
            return []
        out: list[tuple[int, str, str]] = []

        heavy = sum(
            tot for tot, sub in self.sessions.values()
            if tot > 0 and sub / tot >= SUBAGENT_HEAVY_RATIO
        )
        heavy_pct = round(heavy / self.total * 100)
        if heavy_pct > 0:
            out.append((heavy_pct, f"{heavy_pct}% from subagent-heavy sessions",
                        "Each subagent runs its own requests."))

        hc_pct = round(self.high_context / self.total * 100)
        if hc_pct > 0:
            out.append((hc_pct, f"{hc_pct}% at >150k context",
                        "Longer sessions cost more even when cached. /compact or /clear."))

        sub_pct = round(self.subagent / self.total * 100)
        if sub_pct > 0:
            out.append((sub_pct, f"{sub_pct}% from subagents overall",
                        "Total share of cost spent inside subagents."))

        out.sort(key=lambda x: x[0], reverse=True)
        return [(label, desc) for _, label, desc in out]


def _session_id_for(path: str) -> str:
    """Top-level session id for a transcript path.

    Subagent transcripts live at <project>/<session>/subagents/agent-*.jsonl, so
    the session id is the parent-of-parent directory name. Top-level transcripts
    are <project>/<session>.jsonl, so it's the file stem.
    """
    p = Path(path)
    if p.parent.name == "subagents":
        return p.parent.parent.name
    return p.stem


def compute_breakdown(now: datetime | None = None) -> dict[str, list[tuple[str, str]]]:
    """Scan local transcripts and return {'day': [...], 'week': [...]} insights."""
    if not PROJECTS_DIR.exists():
        return {"day": [], "week": []}
    now = now or datetime.now(timezone.utc)
    day = _Accumulator()
    week = _Accumulator()

    files = glob.glob(str(PROJECTS_DIR / "*" / "**" / "*.jsonl"), recursive=True)
    for fpath in files:
        session_id = _session_id_for(fpath)
        is_subagent = "/subagents/" in fpath
        try:
            with open(fpath) as fh:
                for line in fh:
                    if '"assistant"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if o.get("type") != "assistant":
                        continue
                    ts = _parse_ts(o.get("timestamp", ""))
                    if ts is None:
                        continue
                    age_h = (now - ts).total_seconds() / 3600
                    if age_h > 168:
                        continue
                    u = (o.get("message") or {}).get("usage") or {}
                    cost = _msg_cost(u)
                    if cost <= 0:
                        continue
                    high = _context_tokens(u) > HIGH_CONTEXT_TOKENS
                    sidechain = is_subagent or bool(o.get("isSidechain"))
                    week.add(session_id, cost, sidechain, high)
                    if age_h <= 24:
                        day.add(session_id, cost, sidechain, high)
        except Exception as e:
            logger.warning("breakdown: failed reading %s: %s", fpath, e)

    return {"day": day.insights(), "week": week.insights()}
