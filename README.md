# AI Radar

> **macOS only** — requires SwiftBar, Homebrew, and Python 3.

SwiftBar plugins that show AI service usage and limits in your macOS menu bar. Each provider runs as an independent plugin with its own configurable refresh interval.

## Providers

| Provider | Default Refresh | What it shows |
|----------|----------------|---------------|
| **Claude** (Anthropic) | 3 min | 5h/7d usage windows, extra credits, spend (API key), plan tier |
| **Cursor** | 10 min | Premium request usage, billing period reset |
| **Antigravity** | 2 min | Per-model quota (Gemini Pro, Flash, etc.), plan info |
| **Codex** (OpenAI) | 3 min | Rate-limit windows, reset times, credits, plan |

## Install

### Homebrew (recommended)

```bash
brew install longbui99/AIRadar/ai-radar && ai-radar activate
```

A sparkle icon (✦) appears in your menu bar. Click it to enable the providers you want.

**Manager** — click the ✦ icon to toggle providers on/off:

<img src=".github/images/menubar.png" width="280" alt="Manager — toggle providers on/off">

**Provider detail** — shows usage, forecast, and reset time:

<img src=".github/images/claude.png" width="280" alt="Claude provider — usage, forecast, reset time">

### Shell script

```bash
curl -fsSL https://raw.githubusercontent.com/longbui99/AIRadar/main/utils/setup.sh | bash
```

The installer will:
1. Clone the repo to `~/.ai-radar/`
2. Install the `ai-radar` CLI to `~/.local/bin/`
3. Ask which providers you want to enable
4. Install SwiftBar via Homebrew (if missing)
5. Launch the menu bar plugins

Re-run the same command to update.

### Prerequisites

- macOS (not supported on Linux or Windows)
- [Homebrew](https://brew.sh)
- Python 3 (ships with macOS or `brew install python3`)

## Usage

After installation, everything is managed through the `ai-radar` command:

```bash
ai-radar install     # Install SwiftBar + enable configured providers
ai-radar uninstall   # Remove plugins, optionally uninstall SwiftBar
ai-radar config      # Open config.json in your editor
ai-radar status      # Show which providers are active
ai-radar logs        # Tail the plugin log file
ai-radar help        # Show help
```

### Enabling / Disabling Providers

```bash
ai-radar config
```

This opens `config.json` where you toggle providers. Set `"enabled": true` to install, `false` to skip:

```json
{
  "anthropic": {
    "enabled": true,
    "refresh_interval_minutes": 3
  },
  "cursor": {
    "enabled": false,
    "refresh_interval_minutes": 10
  },
  "antigravity": {
    "enabled": false,
    "refresh_interval_minutes": 2
  },
  "codex": {
    "enabled": false,
    "refresh_interval_minutes": 3
  }
}
```

After changing, re-apply with:

```bash
ai-radar install
```

### Changing Refresh Intervals

The `refresh_interval_minutes` value controls how often SwiftBar runs the plugin. It becomes part of the generated filename (e.g. `ai-anthropic.3m.py`), which is how SwiftBar determines the schedule. Re-run `ai-radar install` after changing intervals.

### How It Works

```
config.json ──→ ai-radar install ──→ ai-anthropic.3m.py ──→ symlink ──→ SwiftBar
                       ↑
           utils/plugin.template.py
```

`ai-radar install` auto-discovers providers from `providers/*.py`, reads each provider's config from `config.json`, renders `utils/plugin.template.py`, and writes the result as `ai-{provider}.{interval}m.py`. Generated plugin files are gitignored.

## Configuration

```bash
ai-radar config
```

### Global Settings

| Key | Default | Description |
|-----|---------|-------------|
| `working_hours_per_day` | `24` | Active hours per day for forecast accuracy. Set to `8` if you only code during work hours. |
| `thresholds.yellow` | `80` | Usage % to turn yellow |
| `thresholds.orange` | `90` | Usage % to turn orange |
| `thresholds.red` | `150` | Usage % to turn red |
| `progress_bar.filled` | `█` | Filled character for menu bar |
| `progress_bar.empty` | `▒` | Empty character for menu bar |
| `progress_bar.width` | `4` | Bar width in characters |

### Claude (Anthropic)

Uses your Claude Code OAuth token from macOS Keychain — no API key needed. Just make sure you've logged in via Claude Code first:

```bash
claude login
```

The plugin reads and refreshes the OAuth token automatically.

| Key | Required | Description |
|-----|----------|-------------|
| `anthropic.enabled` | **Yes** | `true` or `false` |
| `anthropic.refresh_interval_minutes` | **Yes** | Refresh interval in minutes |
| `anthropic.api_key` | No | Standard API key — shows rate limits (RPM/TPM) and auto-detects tier |
| `anthropic.admin_key` | No | Admin key — enables spend tracking (today/week/month) |
| `anthropic.monthly_budget` | No | Budget cap for spend bar (e.g. `100`) |

### Cursor

Reads the access token from Cursor IDE's local state database. **Requires Cursor to be installed** — the plugin only works when Cursor is present on your machine.

| Key | Required | Description |
|-----|----------|-------------|
| `cursor.enabled` | **Yes** | `true` or `false` |
| `cursor.refresh_interval_minutes` | **Yes** | Refresh interval in minutes |
| `cursor.session_token` | No | Manual override if auto-discovery fails |

### Antigravity

Connects to Antigravity IDE's local language server. **Requires Antigravity to be installed** — falls back to cloud API using credentials from Antigravity's state database when the IDE is closed.

| Key | Required | Description |
|-----|----------|-------------|
| `antigravity.enabled` | **Yes** | `true` or `false` |
| `antigravity.refresh_interval_minutes` | **Yes** | Refresh interval in minutes |

### Codex

Uses your existing Codex login from `~/.codex/auth.json` and reads the usage
snapshot that Codex writes to local session state. AI Radar never displays,
copies, or stores the token. Log in once with `codex login`, then click the
Codex menu-bar item to see limit windows, reset times, credits, and plan details.

| Key | Required | Description |
|-----|----------|-------------|
| `codex.enabled` | **Yes** | `true` or `false` |
| `codex.refresh_interval_minutes` | **Yes** | Refresh interval in minutes |

## Adding a Provider

1. Create `providers/my_provider.py`:

```python
PROVIDER_KEY = "my_provider"
DISPLAY_NAME = "MP"

def fetch_status(config: dict, global_config: dict | None = None) -> ProviderStatus:
    from providers import ProviderStatus
    # ... fetch and return status
```

2. Add a section to `config.json` via `ai-radar config`:

```json
{
  "my_provider": {
    "enabled": true,
    "refresh_interval_minutes": 5
  }
}
```

3. Re-run `ai-radar install` — providers are auto-discovered from `providers/*.py`.

## Uninstall

### Homebrew

```bash
ai-radar uninstall && brew uninstall ai-radar
```

> **Important:** If you installed via Homebrew, you must also uninstall via Homebrew. Do not mix install methods — running `ai-radar uninstall` alone will remove plugins but leave the Homebrew formula installed.

### Shell script

```bash
ai-radar uninstall
```

Removes plugin symlinks, generated files, and optionally uninstalls SwiftBar.

## Testing

Generate plugins first with `ai-radar install`, then run directly:

```bash
python3 ai-anthropic.3m.py
python3 ai-cursor.10m.py
python3 ai-antigravity.2m.py
```

Logs are written to `ai-radar.log` in the project directory.

## License

GPL-3.0 — free to use, but modifications must be contributed back. See [LICENSE.md](LICENSE.md).

## Author

Built by [Long Bui](https://longbui.net).
