"""Anthropic (Claude) provider for AI Radar."""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from providers import MetricData, ProviderStatus, SpendData

logger = logging.getLogger(__name__)

PROVIDER_KEY = "anthropic"
DISPLAY_NAME = "CLD"
FULL_NAME = "Claude Code"

API_BASE = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
KEYCHAIN_SERVICE = "Claude Code-credentials"
_CACHE_DIR = Path.home() / ".cache" / "ai-radar"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_FILE = _CACHE_DIR / "usage_cache.json"

# Anthropic tier detection: map RPM limits to known tiers and their monthly spend caps.
# Source: https://docs.anthropic.com/en/api/rate-limits
# The RPM limit on the count_tokens endpoint reflects the account tier.
TIER_BY_RPM: dict[int, tuple[str, float]] = {
    50: ("Free", 0),
    1000: ("Build Tier 1", 100),
    2000: ("Build Tier 2", 250),
    4000: ("Build Tier 3", 1000),
    8000: ("Build Tier 4", 5000),
    16000: ("Scale", 0),  # Custom
}


def _read_keychain_raw() -> dict:
    """Read raw Claude Code credentials JSON from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return {}
        return json.loads(result.stdout.strip())
    except Exception:
        return {}


def _get_keychain_account() -> str:
    """Read the account name from the existing keychain entry."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if '"acct"' in line and "<blob>=" in line:
                # Parse: "acct"<blob>="longglacis"
                return line.split('="', 1)[1].rstrip('"')
    except Exception:
        pass
    return ""


def _write_keychain_raw(creds: dict) -> None:
    """Write updated credentials back to macOS Keychain.

    Preserves the original account name so Claude Code extension can still find it.
    """
    try:
        account = _get_keychain_account() or "Claude Code"
        creds_json = json.dumps(creds)
        # Delete then re-add (security CLI doesn't support in-place update)
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True,
            timeout=5,
        )
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                account,
                "-w",
                creds_json,
            ],
            capture_output=True,
            timeout=5,
            check=True,
        )
        logger.info(
            "Updated keychain credentials after token refresh (account=%s)", account
        )
    except Exception as e:
        logger.error(f"Failed to write keychain: {e}")


def _refresh_oauth_token(refresh_token: str, scopes: list[str]) -> dict:
    """Refresh the OAuth access token using the refresh token.

    Returns the full token response: {access_token, refresh_token, expires_in, ...}
    """
    payload = json.dumps(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
            "scope": " ".join(scopes) if scopes else "",
        }
    ).encode()

    req = urllib.request.Request(
        OAUTH_TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "claude-code/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _read_keychain_creds() -> dict:
    """Read Claude Code credentials from macOS Keychain.

    Auto-refreshes the OAuth token if expired, updating the keychain so that
    Claude Code extension and other consumers also get the fresh token.

    Returns dict with keys: access_token, subscription_type, rate_limit_tier, expires_at.
    """
    raw = _read_keychain_raw()
    if not raw:
        return {}

    oauth = raw.get("claudeAiOauth", {})
    access_token = oauth.get("accessToken", "")
    refresh_token = oauth.get("refreshToken", "")
    expires_at = oauth.get("expiresAt", 0)
    scopes = oauth.get("scopes", [])
    logger.info("OAuth credentials: %s", oauth)

    # Only refresh when the token is expired or expiring within 5 minutes
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    token_expiring = expires_at and (expires_at - now_ms) < 5 * 60 * 1000
    if refresh_token and token_expiring:
        logger.info("OAuth token expired or expiring soon, refreshing...")
        try:
            token_resp = _refresh_oauth_token(refresh_token, scopes)
            new_access = token_resp.get("access_token", "")
            new_refresh = token_resp.get("refresh_token", refresh_token)
            expires_in = token_resp.get("expires_in", 3600)
            new_expires_at = int((datetime.utcnow().timestamp() + expires_in) * 1000)

            if new_access:
                access_token = new_access
                oauth["accessToken"] = new_access
                oauth["refreshToken"] = new_refresh
                oauth["expiresAt"] = new_expires_at
                raw["claudeAiOauth"] = oauth
                _write_keychain_raw(raw)
                expires_at = new_expires_at
                logger.info("OAuth token refreshed successfully")
        except Exception as e:
            logger.error(f"Failed to refresh OAuth token: {e}")

    return {
        "access_token": access_token,
        "subscription_type": oauth.get("subscriptionType", ""),
        "rate_limit_tier": oauth.get("rateLimitTier", ""),
        "expires_at": expires_at,
        "scopes": scopes,
    }


# Known subscription plans: key -> (label, monthly_price)
SUBSCRIPTION_INFO: dict[str, tuple[str, float]] = {
    "free": ("Free", 0),
    "pro": ("Pro", 20),
    "team": ("Team", 25),
    "enterprise": ("Enterprise", 0),
    "max_5x": ("Max 5x", 100),
    "max_20x": ("Max 20x", 200),
}

# Rate limit tier -> subscription key mapping
TIER_TO_SUB: dict[str, str] = {
    "default_claude_free": "free",
    "default_claude_pro": "pro",
    "default_claude_team": "team",
    "default_claude_max_5x": "max_5x",
    "default_claude_max_20x": "max_20x",
}


# Default thresholds — overridden by config.json
_THRESHOLDS = {"yellow": 60, "orange": 80, "red": 100}
_WORKING_HOURS: float = 0  # 0 = not configured, use full 24h


def _set_thresholds(cfg: dict) -> None:
    global _THRESHOLDS, _WORKING_HOURS
    t = cfg.get("thresholds", {})
    if t:
        _THRESHOLDS = {
            "yellow": t.get("yellow", 60),
            "orange": t.get("orange", 80),
            "red": t.get("red", 100),
        }
    _WORKING_HOURS = cfg.get("working_hours_per_day", 0)


def _pct_color(pct: float) -> str:
    """Return color based on current usage percentage and configured thresholds."""
    if pct >= _THRESHOLDS["red"]:
        return "red"
    if pct >= _THRESHOLDS["orange"]:
        return "orange"
    if pct >= _THRESHOLDS["yellow"]:
        return "yellow"
    return "green"


def _estimate_active_hours(wall_hours: float, working_hours_per_day: float) -> float:
    """Estimate active hours within a wall-clock duration.

    For unconfigured or 24h work days, returns wall_hours unchanged.
    For shorter work days, caps each 24h period at working_hours_per_day.
    """
    if working_hours_per_day <= 0 or working_hours_per_day >= 24:
        return wall_hours
    full_days = wall_hours / 24
    return full_days * working_hours_per_day


def _forecast_color(utilization: float, resets_at: str, window_hours: float) -> str:
    """Forecast color based on linear projection to end of window.

    When working_hours_per_day is configured, the projection uses active hours:
    rate = utilization / elapsed_active_hours
    projected = utilization + rate × remaining_active_hours
    """
    try:
        clean = resets_at.replace("+00:00", "").replace("Z", "")
        reset_dt = datetime.fromisoformat(clean)
        now = datetime.utcnow()
        remaining_secs = (reset_dt - now).total_seconds()
        total_secs = window_hours * 3600
        elapsed_secs = total_secs - remaining_secs

        if elapsed_secs <= 0:
            return "green"

        elapsed_h = elapsed_secs / 3600
        remaining_h = remaining_secs / 3600
        active_elapsed = _estimate_active_hours(elapsed_h, _WORKING_HOURS)
        active_remaining = _estimate_active_hours(remaining_h, _WORKING_HOURS)

        if active_elapsed <= 0:
            return "green"

        rate = utilization / active_elapsed
        projected = utilization + rate * active_remaining

        if projected >= _THRESHOLDS["red"]:
            return "red"
        if projected >= _THRESHOLDS["orange"]:
            return "orange"
        if projected >= _THRESHOLDS["yellow"]:
            return "yellow"
        return "green"
    except Exception:
        return _pct_color(utilization)


_COLOR_RANK = {"white": 0, "green": 0, "yellow": 1, "orange": 2, "red": 3}


def _to_bar_color(color: str) -> str:
    """Map metric color to menu bar color. Green becomes white for the top bar."""
    return "white" if color == "green" else color


def _worst(a: str, b: str) -> str:
    """Return the more severe color."""
    return a if _COLOR_RANK.get(a, 0) >= _COLOR_RANK.get(b, 0) else b


def _forecast_pct(utilization: float, resets_at: str, window_hours: float) -> float:
    """Return projected utilization at end of window using active-hours model."""
    try:
        clean = resets_at.replace("+00:00", "").replace("Z", "")
        reset_dt = datetime.fromisoformat(clean)
        now = datetime.utcnow()
        remaining_secs = (reset_dt - now).total_seconds()
        total_secs = window_hours * 3600
        elapsed_secs = total_secs - remaining_secs
        if elapsed_secs <= 0:
            return utilization

        elapsed_h = elapsed_secs / 3600
        remaining_h = remaining_secs / 3600
        active_elapsed = _estimate_active_hours(elapsed_h, _WORKING_HOURS)
        active_remaining = _estimate_active_hours(remaining_h, _WORKING_HOURS)

        if active_elapsed <= 0:
            return utilization

        rate = utilization / active_elapsed
        return utilization + rate * active_remaining
    except Exception:
        return utilization


def fetch_status(config: dict, global_config: dict | None = None) -> ProviderStatus:
    """Fetch Claude usage status."""
    if global_config:
        _set_thresholds(global_config)
    api_key = config.get("api_key", "")
    admin_key = config.get("admin_key", "")
    budget = config.get("monthly_budget", 0)
    # Personal credit cap. The API's extra_usage.monthly_limit is the org-wide
    # credit pool (e.g. $5000); set credit_limit to your per-member allowance
    # (e.g. $50) to display that instead.
    credit_limit = config.get("credit_limit", 0)

    # Auto-discover from Claude Code keychain
    keychain_creds = _read_keychain_creds() if not api_key else {}
    keychain_token = keychain_creds.get("access_token", "")

    if not api_key and not admin_key and not keychain_token:
        return ProviderStatus(
            name="Claude (Anthropic)",
            short_name=DISPLAY_NAME,
            summary="--",
            error="No API key configured",
        )

    metrics: list[MetricData] = []
    spend_rows: list[SpendData] = []
    spend_pct: float | None = None
    spend_color = "green"
    spend_forecast = ""
    plan_label = ""
    rate_limits: list[tuple[str, float, str]] = []
    summary = ""
    error = None
    worst_color = "green"
    breakdown: dict[str, list[tuple[str, str]]] = {}

    # Admin key: fetch spend
    if admin_key:
        try:
            spend = _fetch_spend(admin_key)
            monthly = spend["month"]
            spend_rows = [
                SpendData("Today", _fmt_money(spend["today"])),
                SpendData("This Week", _fmt_money(spend["week"])),
                SpendData("This Month", _fmt_money(monthly)),
            ]
            if budget > 0:
                pct = min(monthly / budget * 100, 100) if budget else 0
                spend_pct = pct
                spend_color = _pct_color(pct)
                remaining = max(budget - monthly, 0)
                spend_rows.append(SpendData("Remaining", _fmt_money(remaining)))
                summary = f"{_fmt_money(monthly)}/{_fmt_money(budget)}"
            else:
                summary = f"{_fmt_money(monthly)}"
        except Exception as e:
            error = f"Spend API error: {e}"

    # OAuth token path: fetch real usage via /api/oauth/usage
    if not api_key and not admin_key and keychain_token:
        tier = keychain_creds.get("rate_limit_tier", "")
        sub = keychain_creds.get("subscription_type", "")
        sub_key = TIER_TO_SUB.get(tier, sub)
        sub_label, sub_price = SUBSCRIPTION_INFO.get(sub_key, (sub_key, 0))
        plan_label = sub_label
        try:
            usage = _fetch_oauth_usage_cached(keychain_token)

            five_h = usage.get("five_hour")
            seven_d = usage.get("seven_day")
            extra = usage.get("extra_usage")
            worst_color = "green"
            bar_parts = []

            if five_h:
                pct5 = five_h.get("utilization") or 0
                reset5_raw = five_h.get("resets_at", "")
                fc5 = _forecast_color(pct5, reset5_raw, 5)
                fp5 = _forecast_pct(pct5, reset5_raw, 5)
                worst_color = _worst(worst_color, fc5)
                bar_parts.append(f"5h:{pct5:.0f}%")
                metrics.append(
                    MetricData(
                        label="Session (5h)",
                        short_label="5h",
                        pct=pct5,
                        forecast_pct=fp5,
                        color=fc5,
                        reset_label=_parse_reset(reset5_raw),
                    )
                )

            if seven_d:
                pct7 = seven_d.get("utilization") or 0
                reset7_raw = seven_d.get("resets_at", "")
                fc7 = _forecast_color(pct7, reset7_raw, 168)
                fp7 = _forecast_pct(pct7, reset7_raw, 168)
                worst_color = _worst(worst_color, fc7)
                bar_parts.append(f"7d:{pct7:.0f}%")
                metrics.append(
                    MetricData(
                        label="Weekly (7d)",
                        short_label="7d",
                        pct=pct7,
                        forecast_pct=fp7,
                        color=fc7,
                        reset_label=_parse_reset(reset7_raw),
                    )
                )

            # Per-model weekly limits (e.g. Fable, Sonnet, Opus) are now delivered
            # in the `limits` array as kind="weekly_scoped" entries, each carrying a
            # scope.model.display_name. The legacy seven_day_sonnet/opus fields are
            # kept as a fallback for older API responses.
            seen_scoped: set[str] = set()
            for lim in usage.get("limits") or []:
                if lim.get("kind") != "weekly_scoped":
                    continue
                scope = lim.get("scope") or {}
                model_name = ((scope.get("model") or {}).get("display_name") or "").strip()
                if not model_name:
                    continue
                seen_scoped.add(model_name.lower())
                pct_m = lim.get("percent") or 0
                reset_m_raw = lim.get("resets_at", "")
                fc_m = _forecast_color(pct_m, reset_m_raw, 168)
                fp_m = _forecast_pct(pct_m, reset_m_raw, 168)
                worst_color = _worst(worst_color, fc_m)
                metrics.append(
                    MetricData(
                        label=f"{model_name} (7d)",
                        short_label=model_name[:3],
                        pct=pct_m,
                        forecast_pct=fp_m,
                        color=fc_m,
                        reset_label=_parse_reset(reset_m_raw),
                        detail_only=True,
                    )
                )

            # Legacy fallback: only if the limits array didn't already report Sonnet.
            sonnet = usage.get("seven_day_sonnet")
            if sonnet and "sonnet" not in seen_scoped:
                pct_s = sonnet.get("utilization") or 0
                reset_s_raw = sonnet.get("resets_at", "")
                fc_s = _forecast_color(pct_s, reset_s_raw, 168)
                fp_s = _forecast_pct(pct_s, reset_s_raw, 168)
                worst_color = _worst(worst_color, fc_s)
                metrics.append(
                    MetricData(
                        label="Sonnet (7d)",
                        short_label="Son",
                        pct=pct_s,
                        forecast_pct=fp_s,
                        color=fc_s,
                        reset_label=_parse_reset(reset_s_raw),
                        detail_only=True,
                    )
                )

            if extra and extra.get("is_enabled"):
                used = extra.get("used_credits", 0) or 0
                limit_val = extra.get("monthly_limit") or 0
                epct = extra.get("utilization") or 0
                # Override the org-wide pool limit with the configured personal cap.
                # Note: `used` is what the API reports for the pool, so utilization
                # is only meaningful when your usage maps to your own credit balance.
                if credit_limit > 0:
                    limit_val = credit_limit
                    epct = min((used / credit_limit * 100) if credit_limit else 0, 100)
                now = datetime.utcnow()
                day_of_month = now.day
                days_in_month = 30
                elapsed_active = _estimate_active_hours(day_of_month * 24, _WORKING_HOURS)
                remaining_active = _estimate_active_hours((days_in_month - day_of_month) * 24, _WORKING_HOURS)
                if elapsed_active > 0:
                    rate = epct / elapsed_active
                    projected_pct = epct + rate * remaining_active
                else:
                    projected_pct = epct
                fc_e = _pct_color(projected_pct)
                # Credit/spend status is informational — dropdown-only, and it
                # must NOT drive the menu-bar (main) color or title, which reflect
                # plan usage windows only.
                limit_display = _fmt_money(limit_val) if limit_val else "unlimited"
                metrics.append(
                    MetricData(
                        label="Extra Credits",
                        short_label="$",
                        pct=epct,
                        forecast_pct=projected_pct,
                        color=fc_e,
                        reset_label="month end",
                        extra=f"{_fmt_money(used)} / {limit_display}",
                        detail_only=True,
                    )
                )
            else:
                # Credits disabled (e.g. org out of credits) — surface a status
                # row so it's visible instead of silently omitted.
                reason_raw = (extra or {}).get("disabled_reason") or ""
                reason = {
                    "out_of_credits": "Out of credits",
                    "not_eligible": "Not eligible",
                }.get(reason_raw, reason_raw.replace("_", " ").capitalize() or "Off")
                # Show used/limit if the API still reports a spent amount, otherwise
                # the reason. When out of credits the API zeroes these out.
                used_c = (extra or {}).get("used_credits")
                if used_c is None:
                    minor = ((usage.get("spend") or {}).get("used") or {}).get("amount_minor")
                    used_c = (minor / 100.0) if minor else 0
                disp_limit = credit_limit or (extra or {}).get("monthly_limit") or 0
                if used_c:
                    lim_disp = _fmt_money(disp_limit) if disp_limit else "unlimited"
                    status_text = f"{_fmt_money(used_c)} / {lim_disp} · {reason}"
                else:
                    cap = f" · cap {_fmt_money(disp_limit)}" if disp_limit else ""
                    status_text = f"{reason}{cap}"
                metrics.append(
                    MetricData(
                        label="Extra Credits",
                        short_label="$",
                        pct=0,
                        forecast_pct=0,
                        color="gray",
                        reset_label="",
                        extra=status_text,
                        detail_only=True,
                        status_only=True,
                    )
                )

            summary = " ".join(bar_parts) if bar_parts else "OK"

            # Local "what's driving usage" breakdown (Day/Week), approximate.
            if config.get("usage_breakdown", True):
                try:
                    from providers.usage_breakdown import compute_breakdown
                    breakdown = compute_breakdown()
                except Exception as be:
                    logger.warning("usage breakdown failed: %s", be)

        except Exception as e:
            if sub_price > 0:
                summary = f"{_fmt_money(sub_price)}/mo"
            else:
                summary = sub_label or "Active"
            error = f"Usage API: {e}"

    # Standard API key: fetch rate limits + auto-detect tier
    if api_key:
        try:
            limits = _fetch_rate_limits(api_key)
            if budget <= 0:
                tier_name, tier_budget = _detect_tier(limits)
                budget = tier_budget
                if tier_name:
                    plan_label = tier_name
            if not summary:
                rpm_rem = limits.get("requests_remaining", "?")
                summary = f"{rpm_rem}rpm"
            for prefix, label in [
                ("requests", "RPM"),
                ("input_tokens", "Input TPM"),
                ("output_tokens", "Output TPM"),
            ]:
                lim = limits.get(f"{prefix}_limit")
                rem = limits.get(f"{prefix}_remaining")
                if lim is not None and rem is not None:
                    try:
                        lim_v, rem_v = int(lim), int(rem)
                        used_pct = (lim_v - rem_v) / lim_v * 100 if lim_v else 0
                        rate_limits.append(
                            (label, used_pct, f"{_fmt_num(rem)}/{_fmt_num(lim)}")
                        )
                    except (ValueError, TypeError):
                        rate_limits.append((label, 0, f"{rem}/{lim}"))
        except Exception as e:
            if not summary:
                summary = "err"
            error = f"Rate limit error: {e}"

    return ProviderStatus(
        name="Claude (Anthropic)",
        short_name=DISPLAY_NAME,
        summary=summary or "--",
        color=_to_bar_color(worst_color),
        error=error,
        metrics=metrics,
        spend_rows=spend_rows,
        spend_pct=spend_pct,
        spend_color=spend_color,
        spend_forecast=spend_forecast,
        plan_label=plan_label,
        rate_limits=rate_limits,
        breakdown=breakdown,
    )


def _detect_tier(limits: dict) -> tuple[str, float]:
    """Detect Anthropic tier from RPM limit. Returns (tier_name, monthly_budget)."""
    try:
        rpm_limit = int(limits.get("requests_limit", 0))
    except (ValueError, TypeError):
        return ("", 0)
    best_name, best_budget = "", 0.0
    for rpm, (name, budget) in TIER_BY_RPM.items():
        if rpm_limit >= rpm:
            best_name, best_budget = name, budget
    return (best_name, best_budget)


def _fmt_num(n: int | str) -> str:
    try:
        v = int(n)
    except (ValueError, TypeError):
        return str(n)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(v)


def _fmt_money(amount: float) -> str:
    try:
        amount = float(amount)
        if amount >= 1000:
            return f"${amount / 1000:.1f}k".replace(".0k", "k")
        if amount == int(amount):
            return f"${int(amount)}"
        return f"${amount:.2f}"
    except (ValueError, TypeError):
        return f"${amount}"


def _fetch_oauth_usage(oauth_token: str) -> dict:
    """Fetch usage data via the OAuth usage endpoint (same as Claude Code /usage).

    Response structure:
    {
        "five_hour":  {"utilization": float, "resets_at": str},
        "seven_day":  {"utilization": float, "resets_at": str},
        "seven_day_sonnet": {"utilization": float, "resets_at": str} | null,
        "extra_usage": {"is_enabled": bool, "monthly_limit": int,
                        "used_credits": float, "utilization": float} | null,
    }
    """
    req = urllib.request.Request(
        f"{API_BASE}/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {oauth_token}",
            "anthropic-version": API_VERSION,
            "anthropic-beta": "oauth-2025-04-20",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _force_refresh_token() -> str:
    """Force-refresh the OAuth token regardless of expiry. Returns new access token or empty string."""
    raw = _read_keychain_raw()
    if not raw:
        return ""
    oauth = raw.get("claudeAiOauth", {})
    refresh_token = oauth.get("refreshToken", "")
    scopes = oauth.get("scopes", [])
    if not refresh_token:
        return ""
    try:
        token_resp = _refresh_oauth_token(refresh_token, scopes)
        new_access = token_resp.get("access_token", "")
        new_refresh = token_resp.get("refresh_token", refresh_token)
        expires_in = token_resp.get("expires_in", 3600)
        new_expires_at = int((datetime.utcnow().timestamp() + expires_in) * 1000)
        if new_access:
            oauth["accessToken"] = new_access
            oauth["refreshToken"] = new_refresh
            oauth["expiresAt"] = new_expires_at
            raw["claudeAiOauth"] = oauth
            _write_keychain_raw(raw)
            logger.info("Force-refreshed OAuth token after 429")
            return new_access
    except Exception as e:
        logger.error(f"Failed to force-refresh OAuth token: {e}")
    return ""


def _fetch_oauth_usage_cached(oauth_token: str) -> dict:
    """Fetch usage with token refresh retry and file cache fallback for 429 rate limits."""
    try:
        data = _fetch_oauth_usage(oauth_token)
        # Save to cache on success
        try:
            _CACHE_FILE.write_text(json.dumps(data))
        except Exception:
            pass
        logger.info(
            "Successfully fetched data directly from active Anthropic API (online)"
        )
        return data
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500:
            # Try refreshing the token and retrying once for any 4xx error
            logger.warning(f"Hit {e.code} from Anthropic API. Attempting token refresh and retry...")
            new_token = _force_refresh_token()
            if new_token and new_token != oauth_token:
                try:
                    data = _fetch_oauth_usage(new_token)
                    try:
                        _CACHE_FILE.write_text(json.dumps(data))
                    except Exception:
                        pass
                    logger.info("Retry with refreshed token succeeded")
                    return data
                except Exception as retry_err:
                    logger.warning(f"Retry after token refresh also failed: {retry_err}")
            # Fall back to cache
            if _CACHE_FILE.exists():
                logger.warning(
                    f"Falling back to cached data after {e.code}"
                )
                return json.loads(_CACHE_FILE.read_text())
        raise


def _parse_reset(iso_str: str) -> str:
    """Parse ISO reset time to a string matching Claude terminal style.

    Returns e.g. "2h 14m — Apr 1 at 5:00 PM (Asia/Saigon)"
    """
    try:
        clean = iso_str.replace("+00:00", "").replace("Z", "")
        reset_utc = datetime.fromisoformat(clean)
        now = datetime.utcnow()
        delta = reset_utc - now
        total_min = int(delta.total_seconds() / 60)

        # Convert to local timezone
        reset_aware = reset_utc.replace(tzinfo=timezone.utc)
        reset_local = reset_aware.astimezone()
        # Get short timezone name — prefer city part of IANA name (e.g. "Saigon")
        tz_name = reset_local.strftime("%Z")  # fallback e.g. "+07"
        try:
            link = subprocess.run(
                ["readlink", "/etc/localtime"], capture_output=True, text=True, timeout=2
            ).stdout.strip()
            if "zoneinfo/" in link:
                iana = link.split("zoneinfo/", 1)[1]
                # Use just the city part: "Asia/Ho_Chi_Minh" -> "Ho_Chi_Minh"
                tz_name = iana.split("/")[-1].replace("_", " ")
        except Exception:
            pass

        if total_min < 0:
            return "now"

        # Relative part
        if total_min < 60:
            relative = f"{total_min}m"
        elif total_min < 24 * 60:
            hours = total_min // 60
            mins = total_min % 60
            relative = f"{hours}h {mins}m"
        else:
            hours = total_min // 60
            days = hours // 24
            relative = f"{days}d {hours % 24}h"

        # Absolute part — match Claude terminal style
        now_local = datetime.now().astimezone()
        if reset_local.date() == now_local.date():
            abs_time = reset_local.strftime("%-I:%M %p")
        else:
            abs_time = (
                f"{reset_local.strftime('%b %-d')} at {reset_local.strftime('%-I:%M %p')}"
            )

        return f"{relative} — {abs_time} ({tz_name})"
    except Exception:
        return iso_str


def _auth_headers(key: str) -> dict[str, str]:
    """Return appropriate auth headers for API key or OAuth token."""
    if key.startswith("sk-ant-oat"):
        return {"Authorization": f"Bearer {key}"}
    return {"x-api-key": key}


def _fetch_rate_limits(api_key: str) -> dict:
    """Call count_tokens to read rate limit headers without consuming tokens."""
    payload = json.dumps(
        {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()

    req = urllib.request.Request(
        f"{API_BASE}/v1/messages/count_tokens",
        data=payload,
        headers={
            **_auth_headers(api_key),
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        headers = resp.headers
        limits = {}
        for key in ("requests", "tokens", "input_tokens", "output_tokens"):
            for suffix in ("limit", "remaining", "reset"):
                h = f"anthropic-ratelimit-{key.replace('_', '-')}-{suffix}"
                val = headers.get(h)
                if val is not None:
                    limits[f"{key}_{suffix}"] = val
        return limits


def _fetch_spend(admin_key: str) -> dict[str, float]:
    """Fetch cost data for today, this week, this month."""
    now = datetime.utcnow()
    month_start = now.replace(day=1).strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    costs = _get_cost_report(admin_key, month_start, tomorrow)

    month_total = 0.0
    week_total = 0.0
    today_total = 0.0

    for bucket in costs:
        date_str = bucket.get("date", "")
        amount = 0.0
        for cost_item in bucket.get("costs", []):
            try:
                amount += float(cost_item.get("amount", 0))
            except (ValueError, TypeError):
                pass
        dollars = amount / 100.0
        month_total += dollars
        if date_str >= week_start:
            week_total += dollars
        if date_str == today:
            today_total += dollars

    return {"today": today_total, "week": week_total, "month": month_total}


def _get_cost_report(admin_key: str, start: str, end: str) -> list:
    """Paginated fetch of cost report."""
    all_data: list = []
    next_page = None

    while True:
        url = (
            f"{API_BASE}/v1/organizations/cost_report"
            f"?start_date={start}&end_date={end}&bucket_size=1d"
        )
        if next_page:
            url += f"&next_page={next_page}"

        req = urllib.request.Request(
            url,
            headers={
                "x-api-key": admin_key,
                "anthropic-version": API_VERSION,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            all_data.extend(data.get("data", []))
            if data.get("has_more"):
                next_page = data.get("next_page")
            else:
                break

    return all_data
