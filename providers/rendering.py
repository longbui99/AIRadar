"""Rendering engine for AI Radar — beautiful SwiftBar output."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

# ── Color palette (macOS system colors) ──────────────────────────────────────
COLORS = {
    "green": "#34C759",
    "yellow": "#FFD60A",
    "orange": "#FF9500",
    "red": "#E5484D",
    "blue": "#007AFF",
    "purple": "#AF52DE",
    "cyan": "#32ADE6",
    "gray": "#8E8E93",
    "dim": "#636366",
    "label": "#AEAEB2",
    "white": "#F5F5F7",
}

# Per-provider brand colors for quick identification
PROVIDER_COLORS: dict[str, str] = {
    "CLD": "#D97706",   # Anthropic — warm amber
    "CUR": "#A855F7",   # Cursor — purple
    "AG": "#10B981",    # Antigravity — teal
    "CDX": "#10A37F",   # OpenAI — green
}

# Per-provider 16x16 template images (base64-encoded PNG)
PROVIDER_LOGOS: dict[str, str] = {
    # Claude sunburst/asterisk logo
    "CLD": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABk0lEQVR4nH2Sv0uVcRTGP9+rhlxNInUwwyJpqnBoaW68gxBuVv9EkDhZNAVlQUMQBRLkkBgRNWRLSzq1RRA0RGvgYOAPkPzEwfPK5XLvPfDyfs97nud8z3ueB7qEOqSe7IY5CrWoNbWhjmS+pH7O83DWAlNoF2rdw3iR+Yr6Nc/Ps1Zv20Q9lu/ZJuADdVUdyG+znW5uqJvqzcw/qg/VOfWpuqiuZW1G/alercg19ZT6TD1QP6iX1fUkvlG/qBdzmn11WT0f3NIyST9wB7gERO0KcBx4BwwC34CFUspexSnqCeA+cABsAr+BCWAamIq+wC/gLfADOA2MAHXgXi8Qz9kEDmWjM8BwNVhOEcTtrI1mg/52v7AAXAC2gEY2XQLGge/A3VLKbsWp5RLHYonATo4dexgDXgLvgXPAbWAS+JsLD06tk4xr6iP1lvpEfax+apLxT3A6GelGi5FCxspI15ux7QzVo26przN/pW60WrmZEwocGaqU8k+9FrKl1+OmvjzPp5R7kZdSQp3uoU6E47qB/gOQuIqURS0s0gAAAABJRU5ErkJggg==",
    # Cursor 3D cube with arrow logo
    "CUR": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAB2klEQVR4nKVTPasaQRS9d2cZ3S9WVLZ9jbxCwpouIMJaCelsbCzfDwhBCPtAkkUQIYF0FvkHAQtthRT+hKQwFsE07w/IslusurMTxvf2sVnNIyGnmeHOuefee2YG4D+BT5xJAMA9zztxRqNR8teijuPI+eBsNiOc87OCeKHqqZJt288sy/rQaDQOiqIMx+PxdxEXIojI0wQ5J5Y4jlNijN1SSl8fj8eCJEkgSdLL4XD4KQiCj4h4lxWRMpWh1Wq9QMRv+/3etW27MJlMmKqqzPd9Sil9VSqVvrqu20ZE6PV65DHRcZyTYYj4XJblqziOoyRJeLPZJK7rkna7zX3fjxhjZULIteDW63XMdpBCJCYPo6HYFotF6Ha7OBgM5FqtxsMw3D+yLwiAmI9zDqJNgXs9gHK5DKqqAmPsN76cSxamoaZpsa7rsjBQCMznc1gul6xarcqmaRbOBCzLEo4iIeSHYRi+oigmpZRvt9tkOp3ier0GwzAKURQFlUplK7ibzYbn3wGKJvr9/lUQBO9M07w5HA4QhiHoug5xHH/e7XZvV6vVz5SbF4DsQafTaWqa9p4QYjDG3iwWiy+pR9mHdAlSescC6V6snuedmf5HZMlZwX+FGOmpHwu/ABVouKZUEbMGAAAAAElFTkSuQmCC",
    # Antigravity 'A' hill/flame logo
    "AG": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABJklEQVR4nJWTMS8FURCFz6wt0MhLqES8SCREq/JPVJT8hdf5B4oXhd9AKTqhkOg1Eq3ea+Xhk8vZdd9a+5jkZmYzs2fuzDlX6jCgAGa6aloNiPxHA4X+a8AKsJ0D/6VzAAvAEBjxZWdA37miC6C0P+DbXu1vp46D0YEbYOyT7MX+0Pl6P0V+/Yh4B+YlrWe5t8xvOa5vULZcpCdpyQAVSNXxoVlcZnFCRdKqpGdJe5L6PheSjiWNugCKNKSkZUmPEXHZ2M+5pCfPT+sIEYkldhOIC8M1Y0mbkuYi4rpiq42+gbd9Xy02y506N5igE1MC7LvgCtjIRPUpHqAHnLgm7aOmPQU75vqoEslvYnGju4k8sAisZd8/5JrLGJhtlXQaZ9qDaT7vD6VbNDWiEUxAAAAAAElFTkSuQmCC",
}


def _provider_color(short_name: str) -> str:
    """Return brand color for a provider, falling back to blue."""
    return PROVIDER_COLORS.get(short_name, COLORS["blue"])

# ── SF Symbol helpers ────────────────────────────────────────────────────────

def _sfconfig(colors: list[str], rendering: str = "Palette", weight: str = "medium") -> str:
    """Encode SF Symbol config as base64 for sfconfig= parameter."""
    cfg = {"renderingMode": rendering, "colors": colors, "weight": weight}
    return base64.b64encode(json.dumps(cfg).encode()).decode()


def _color_hex(color: str) -> str:
    """Map status color name to hex."""
    return COLORS.get(color, color)


def _status_symbol(color: str) -> str:
    """Pick an SF Symbol name based on status severity."""
    if color == "red":
        return "exclamationmark.triangle.fill"
    if color == "orange":
        return "flame.fill"
    return "sparkles"


# ── Compact bar for menu bar title ───────────────────────────────────────────

# Defaults — overridden by config.json "progress_bar" section
_BAR_FILLED = "\u25A0"
_BAR_EMPTY = "\u25A1"
_BAR_WIDTH = 4


# Defaults — overridden by config.json "thresholds" section
_THRESHOLDS = {"yellow": 60, "orange": 80, "red": 100}


def configure_bar(cfg: dict) -> None:
    """Load progress bar and threshold settings from config."""
    global _BAR_FILLED, _BAR_EMPTY, _BAR_WIDTH, _THRESHOLDS
    bar_cfg = cfg.get("progress_bar", {})
    if bar_cfg:
        _BAR_FILLED = bar_cfg.get("filled", _BAR_FILLED)
        _BAR_EMPTY = bar_cfg.get("empty", _BAR_EMPTY)
        _BAR_WIDTH = bar_cfg.get("width", _BAR_WIDTH)
    t = cfg.get("thresholds", {})
    if t:
        _THRESHOLDS = {
            "yellow": t.get("yellow", 60),
            "orange": t.get("orange", 80),
            "red": t.get("red", 100),
        }


def _pct_color(pct: float) -> str:
    """Return color based on configured thresholds."""
    if pct >= _THRESHOLDS["red"]:
        return "red"
    if pct >= _THRESHOLDS["orange"]:
        return "orange"
    if pct >= _THRESHOLDS["yellow"]:
        return "yellow"
    return "green"


def _mini_bar(pct: float, width: int | None = None) -> str:
    """A compact progress bar using configurable filled/empty characters."""
    w = width if width is not None else _BAR_WIDTH
    pct = max(0.0, min(100.0, pct))
    filled = round(pct / 100.0 * w)
    return _BAR_FILLED * filled + _BAR_EMPTY * (w - filled)


# ── Menu bar title builders ──────────────────────────────────────────────────

def render_title_cycling(statuses: list) -> list[str]:
    """Generate a single title line combining all providers with metrics.

    Only providers with actual metric data appear in the menu bar title.
    Providers without metrics (e.g. unconfigured Cursor) are dropdown-only.
    This avoids SwiftBar cycling between a detailed line and an empty one.

    Format: 5h ▁▂ 7%  7d █▅ 56%  $2.1k/$5k
    """
    if not statuses:
        return ["AI | sfimage=gear color=#FF9500 size=13"]

    # Collect parts from all providers that have metrics
    all_parts: list[str] = []
    worst_overall = "green"

    for s in statuses:
        if not hasattr(s, "metrics") or not s.metrics:
            continue

        worst = s.color or "green"
        rank = {"green": 0, "white": 0, "yellow": 1, "orange": 2, "red": 3}
        worst_overall = worst if rank.get(worst, 0) > rank.get(worst_overall, 0) else worst_overall

        for m in s.metrics:
            if getattr(m, "detail_only", False):
                continue
            if m.short_label == "$":
                all_parts.append(f"{m.extra or f'${m.pct:.0f}%'}")
            elif m.pct < 0:
                # Unlimited — show text instead of symbol
                all_parts.append("Unlimited")
            else:
                bar = _mini_bar(m.pct)
                all_parts.append(f"{m.short_label} {bar} {m.pct:.0f}%")

    if not all_parts:
        # No provider has metrics — show provider name as fallback
        hex_c = _color_hex("green")
        short_name = statuses[0].short_name if statuses else ""
        name = statuses[0].name if statuses else "AI"
        logo = PROVIDER_LOGOS.get(short_name, "")
        if logo:
            return [f" {short_name} | templateImage={logo} size=12 font=SF-Mono-Medium color={hex_c}"]
        return [f" {short_name} | sfimage=sparkles sfsize=14 size=12 font=SF-Mono-Medium color={hex_c}"]

    hex_c = _color_hex(worst_overall)
    # Pick provider-specific logo (fall back to generic sparkles SF Symbol)
    short_name = statuses[0].short_name if statuses else ""
    logo = PROVIDER_LOGOS.get(short_name, "")
    if logo:
        return [
            f" {' · '.join(all_parts)} "
            f"| templateImage={logo} "
            f"size=11 font=SF-Mono-Medium color={hex_c}"
        ]
    return [
        f" {' · '.join(all_parts)} "
        f"| sfimage=sparkles size=11 font=SF-Mono-Medium color={hex_c}"
    ]


# ── Dropdown section builders ────────────────────────────────────────────────

@dataclass
class Metric:
    """A single usage metric for structured rendering."""
    label: str
    pct: float
    forecast_pct: float
    color: str
    reset_label: str
    extra: str = ""
    status_only: bool = False


@dataclass
class SpendRow:
    """A spend breakdown row."""
    label: str
    amount: str


def _smooth_bar(pct: float, width: int = 20) -> str:
    """Create a smooth gradient progress bar using Unicode blocks."""
    blocks = [" ", "\u258f", "\u258e", "\u258d", "\u258c", "\u258b", "\u258a", "\u2589", "\u2588"]
    pct = max(0.0, min(100.0, pct))
    fill = pct / 100.0 * width
    result = []
    for i in range(width):
        if fill >= i + 1:
            result.append("\u2588")
        elif fill > i:
            frac = fill - i
            idx = int(round(frac * 8))
            result.append(blocks[max(0, min(8, idx))])
        else:
            result.append("\u2005")  # thin space
    return "".join(result)


def _forecast_arrow(forecast_pct: float) -> str:
    """Return a directional indicator based on configured thresholds."""
    if forecast_pct >= _THRESHOLDS["red"]:
        return "\u2191\u2191"  # double up arrow
    if forecast_pct >= _THRESHOLDS["orange"]:
        return "\u2197"   # up-right
    if forecast_pct >= _THRESHOLDS["yellow"]:
        return "\u2192"   # right
    return "\u2192"       # right (safe/low)


def render_metric_section(m: Metric) -> list[str]:
    """Render a single metric as a beautiful dropdown section."""
    lines = []
    hex_c = _color_hex(m.color)

    if m.color == "red":
        header_icon = "exclamationmark.triangle.fill"
    elif m.color == "orange":
        header_icon = "chart.line.uptrend.xyaxis"
    elif m.color == "yellow":
        header_icon = "exclamationmark.circle.fill"
    else:
        header_icon = "checkmark.circle.fill"

    if getattr(m, "status_only", False):
        header_icon = "creditcard.fill"

    header_cfg = _sfconfig([hex_c], weight="semibold")
    lines.append(
        f"  {m.label} | sfimage={header_icon} "
        f"sfconfig={header_cfg} sfsize=14 size=13 font=SF-Pro-Text-Semibold"
    )

    # Status-only metrics (e.g. disabled credits) show just a text line — no
    # progress bar or forecast, which would be meaningless without a value.
    if getattr(m, "status_only", False):
        if m.extra:
            lines.append(f"  {m.extra} | size=12 color={hex_c}")
        return lines

    if m.pct < 0:
        # Unlimited — full light gray bar
        bar = "░" * 20
        lines.append(
            f"  {bar}    ∞ | font=SF-Mono-Regular size=12 color={COLORS['label']} trim=false"
        )
    else:
        # Normal metric with progress bar and forecast
        bar = _smooth_bar(m.pct)
        lines.append(
            f"  {bar}  {m.pct:5.1f}% | font=SF-Mono-Regular size=12 color={hex_c} trim=false"
        )

        arrow = _forecast_arrow(m.forecast_pct)
        lines.append(
            f"  {arrow} Forecast {m.forecast_pct:.0f}% at reset | "
            f"font=SF-Pro-Text-Regular size=11 color={hex_c}"
        )

    if m.reset_label:
        lines.append(
            f"  Resets {m.reset_label} | "
            f"sfimage=clock size=11 color={COLORS['dim']}"
        )

    if m.extra:
        lines.append(f"  {m.extra} | size=11 color={COLORS['label']}")

    return lines


def render_spend_section(title: str, rows: list[SpendRow], bar_pct: float | None = None,
                         bar_color: str = "green", forecast_line: str = "") -> list[str]:
    """Render a spending section with optional progress bar."""
    lines = []
    hex_c = _color_hex(bar_color)
    cfg = _sfconfig([hex_c], weight="semibold")
    lines.append(
        f"  {title} | sfimage=creditcard.fill "
        f"sfconfig={cfg} sfsize=14 size=13 font=SF-Pro-Text-Semibold"
    )
    if bar_pct is not None:
        bar = _smooth_bar(bar_pct)
        lines.append(
            f"  {bar}  {bar_pct:5.1f}% | font=SF-Mono-Regular size=12 color={hex_c} trim=false"
        )
    if forecast_line:
        lines.append(f"  {forecast_line} | size=11 color={hex_c}")
    for row in rows:
        lines.append(
            f"  {row.label}  {row.amount} | font=SF-Mono-Regular size=12 color={COLORS['white']}"
        )
    return lines


def render_plan_badge(plan_label: str) -> list[str]:
    """Render the subscription plan as a styled footer."""
    cfg = _sfconfig([COLORS["blue"]], weight="medium")
    return [
        f"  {plan_label} | sfimage=person.crop.circle.badge.checkmark "
        f"sfconfig={cfg} size=12 color={COLORS['label']}"
    ]


def render_rate_limit_section(limits_details: list[tuple[str, float, str]]) -> list[str]:
    """Render rate limits section."""
    lines = []
    cfg = _sfconfig([COLORS["blue"]], weight="semibold")
    lines.append(
        f"  Rate Limits | sfimage=gauge.with.dots.needle.33percent "
        f"sfconfig={cfg} sfsize=14 size=13 font=SF-Pro-Text-Semibold"
    )
    for label, pct, text in limits_details:
        hex_c = _color_hex(_pct_color(pct))
        bar = _smooth_bar(pct, 12)
        lines.append(
            f"  {bar} {text} | font=SF-Mono-Regular size=11 color={hex_c} trim=false"
        )
    return lines


def render_breakdown_section(breakdown: dict) -> list[str]:
    """Render the local "what's driving usage" breakdown (Day + Week groups)."""
    if not breakdown or not (breakdown.get("day") or breakdown.get("week")):
        return []
    lines = []
    cfg = _sfconfig([COLORS["blue"]], weight="semibold")
    lines.append(
        f"  What's Driving Usage | sfimage=chart.pie.fill "
        f"sfconfig={cfg} sfsize=14 size=13 font=SF-Pro-Text-Semibold"
    )
    for key, title in (("day", "Last 24h"), ("week", "Last 7 days")):
        insights = breakdown.get(key) or []
        if not insights:
            continue
        lines.append(
            f"  {title} | size=11 font=SF-Pro-Text-Semibold color={COLORS['label']}"
        )
        for label, desc in insights:
            lower = label.lower()
            match = re.search(r"(\d+(?:\.\d+)?)%", f"{label} {desc}")
            pct = float(match.group(1)) if match else None

            # Semantic colors make healthy cache reuse distinct from expensive
            # context/subagent shares. These thresholds describe contribution,
            # not the provider's quota warning thresholds.
            if "cache" in lower:
                color = COLORS["green"]
            elif "model" in lower or (":" in label and "tokens" in lower):
                color = COLORS["purple"]
            elif "tokens ·" in lower:
                color = COLORS["cyan"]
            elif pct is not None:
                if pct >= 75:
                    color = COLORS["red"]
                elif pct >= 40:
                    color = COLORS["orange"]
                elif pct >= 15:
                    color = COLORS["yellow"]
                else:
                    color = COLORS["green"]
            else:
                color = COLORS["white"]

            if pct is not None:
                marker = _smooth_bar(pct, 6)
            else:
                marker = "●"
            lines.append(
                f"  {marker}  {label} | font=SF-Mono-Regular size=11 "
                f"color={color} trim=false"
            )
            if desc:
                lines.append(
                    f"    {desc} | size=10 color={COLORS['dim']}"
                )
    lines.append(
        f"  Approximate · local sessions only | size=10 color={COLORS['dim']}"
    )
    return lines


def render_separator() -> str:
    return "---"


def render_footer(
    script_dir: str, show_login: bool = False, auth_cli: str = "claude"
) -> list[str]:
    lines = ["---"]
    label = "Codex" if auth_cli == "codex" else "Claude"
    action = "login" if show_login else "logout"
    icon = "plus" if show_login else "minus"
    color = COLORS["blue"] if show_login else COLORS["dim"]
    if show_login:
        lines.append(
            f"Login to {label} | bash=/usr/bin/env param1={auth_cli} param2={action} "
            f"terminal=true refresh=true sfimage=person.crop.circle.badge.{icon} size=13 color={color}"
        )
    else:
        lines.append(
            f"Logout | bash=/usr/bin/env param1={auth_cli} param2={action} "
            f"terminal=true refresh=true sfimage=person.crop.circle.badge.{icon} size=13 color={color}"
        )
    lines.extend([
        f"Open Config | bash=open param1={script_dir}/config.json terminal=false "
        f"sfimage=gearshape size=13 color={COLORS['dim']}",
        f"Refresh | refresh=true sfimage=arrow.clockwise size=13 color={COLORS['dim']}",
    ])
    return lines
