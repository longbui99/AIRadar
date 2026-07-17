"""Shared plugin runner for per-provider SwiftBar scripts."""
from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

from providers import ProviderStatus
from providers.rendering import (
    COLORS,
    Metric,
    SpendRow,
    _provider_color,
    _sfconfig,
    configure_bar,
    render_breakdown_section,
    render_footer,
    render_metric_section,
    render_plan_badge,
    render_rate_limit_section,
    render_spend_section,
    render_title_cycling,
)

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    config_path = SCRIPT_DIR / "config.json"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return json.load(f)


def render_single(status: ProviderStatus) -> None:
    """Render a single provider as a complete SwiftBar menu."""
    needs_login = bool(
        status.error
        and ("No API key" in status.error or "401" in status.error or "Unauthorized" in status.error)
    ) or status.summary in ("--", "err")

    # Title line — single provider
    title_lines = render_title_cycling([status])
    for line in title_lines:
        print(line)

    print("---")

    # Provider header
    pcolor = _provider_color(status.short_name)
    if status.plan_label:
        badge_cfg = _sfconfig([COLORS["blue"]], weight="medium")
        print(
            f"{status.name}  ·  {status.plan_label} | size=13 font=SF-Pro-Text-Semibold "
            f"sfimage=person.crop.circle.badge.checkmark sfconfig={badge_cfg} color={COLORS['blue']}"
        )
    else:
        pcfg = _sfconfig([pcolor], weight="semibold")
        print(
            f"{status.name} | size=13 font=SF-Pro-Text-Semibold "
            f"sfimage=sparkles sfconfig={pcfg} color={COLORS['blue']}"
        )

    # Metrics
    light_sep = f"  {'─' * 30} | size=5 color={COLORS['dim']}"
    for i, md in enumerate(status.metrics):
        if i > 0:
            print(light_sep)
        m = Metric(
            label=md.label,
            pct=md.pct,
            forecast_pct=md.forecast_pct,
            color=md.color,
            reset_label=md.reset_label,
            extra=md.extra,
            status_only=getattr(md, "status_only", False),
        )
        for line in render_metric_section(m):
            print(line)

    # Spend
    if status.spend_rows:
        print(light_sep)
        rows = [SpendRow(label=sr.label, amount=sr.amount) for sr in status.spend_rows]
        for line in render_spend_section(
            "Spend", rows,
            bar_pct=status.spend_pct,
            bar_color=status.spend_color,
            forecast_line=status.spend_forecast,
        ):
            print(line)

    # Rate limits
    if status.rate_limits:
        print(light_sep)
        for line in render_rate_limit_section(status.rate_limits):
            print(line)

    # Local usage breakdown (what's driving usage — Day/Week)
    breakdown_lines = render_breakdown_section(getattr(status, "breakdown", {}))
    if breakdown_lines:
        print(light_sep)
        for line in breakdown_lines:
            print(line)

    # Error
    if status.error:
        print(f"  {status.error} | sfimage=exclamationmark.triangle color=#E5484D size=11")

    # Footer
    for line in render_footer(
        str(SCRIPT_DIR), show_login=needs_login, auth_cli=status.auth_cli
    ):
        print(line)


def run_provider(provider_key: str) -> None:
    """Entry point for a per-provider SwiftBar plugin."""
    logging.basicConfig(
        filename=SCRIPT_DIR / "ai-radar.log",
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        config = load_config()
        configure_bar(config)

        provider_config = config.get(provider_key, {})
        if not provider_config.get("enabled", True):
            return

        mod = importlib.import_module(f"providers.{provider_key}")
        logger.info(f"Fetching status for provider: {provider_key}")
        status = mod.fetch_status(provider_config, global_config=config)
        logger.info(f"Fetched data for {provider_key}: {status}")
        render_single(status)
    except Exception as e:
        logger.exception(f"Error during {provider_key} refresh: {e}")
        print(f"err | sfimage=exclamationmark.triangle color=#E5484D size=13")
        print("---")
        print(f"Error: {e}")
