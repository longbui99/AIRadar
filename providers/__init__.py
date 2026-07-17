"""Provider auto-discovery and shared data structures."""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MetricData:
    """Structured metric for rich rendering."""
    label: str          # e.g. "Session (5h)"
    short_label: str    # e.g. "5h" — for menu bar title
    pct: float          # current utilization 0-100
    forecast_pct: float # projected utilization at reset
    color: str          # forecast color: green/orange/red
    reset_label: str    # e.g. "2h 14m"
    extra: str = ""     # optional (e.g. "$2.1k / $5k")
    detail_only: bool = False  # if True, show in dropdown only, not menu bar
    status_only: bool = False  # if True, render header + extra text only (no bar/forecast)


@dataclass
class SpendData:
    """Structured spend row."""
    label: str
    amount: str


@dataclass
class ProviderStatus:
    """Status returned by each provider's fetch_status()."""

    name: str
    short_name: str
    summary: str
    details: list[str] = field(default_factory=list)
    error: str | None = None
    color: str | None = None
    # Structured data for rich rendering
    metrics: list[MetricData] = field(default_factory=list)
    spend_rows: list[SpendData] = field(default_factory=list)
    spend_pct: float | None = None
    spend_color: str = "green"
    spend_forecast: str = ""
    plan_label: str = ""
    rate_limits: list[tuple[str, float, str]] = field(default_factory=list)
    # Local usage breakdown: {"day": [(label, desc), ...], "week": [...]}
    breakdown: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    # Optional CLI used by the footer for providers with their own login flow.
    auth_cli: str = "claude"


def load_providers() -> dict[str, object]:
    """Auto-discover provider modules in this package."""
    providers: dict[str, object] = {}
    package_dir = Path(__file__).parent

    for _, name, _ in pkgutil.iter_modules([str(package_dir)]):
        if name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__package__}.{name}")
            if hasattr(mod, "PROVIDER_KEY") and hasattr(mod, "fetch_status"):
                providers[mod.PROVIDER_KEY] = mod
        except Exception:
            continue
    return providers
