# Nova Agent

Nova Agent is an open-source AI agent that helps users accomplish tasks through a terminal interface, messaging gateways, tools, skills, and persistent memory. It supports multiple model providers, a rich toolset, scheduled automations, and a closed learning loop that creates and improves skills from experience.

This repository is the official source for Nova Agent and uses GitHub for distribution, releases, and updates.

## Table of Contents

- [Features](#features)
- [Supported Operating Systems](#supported-operating-systems)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Uninstall](#uninstall)
- [Automatic Updates](#automatic-updates)
- [Release Notes](#release-notes)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Security and Privacy](#security-and-privacy)
- [Contributing](#contributing)
- [License](#license)

## Features

- Interactive terminal UI with streaming tool output.
- Messaging gateway support for Telegram, Discord, Slack, WhatsApp, Signal, and more.
- Persistent, agent-curated memory and user profiles.
- Autonomous skill creation and self-improvement during use.
- Scheduled cron jobs with delivery to any platform.
- Delegation to isolated subagents for parallel workstreams.
- Multiple terminal backends (local, Docker, SSH, and others).
- Model provider flexibility: choose any supported provider without code changes.
- Full-text session search for cross-session recall.
- MCP integration for connecting external tool servers.
- Plugin and skills ecosystem.

## Supported Operating Systems

- Linux
- macOS
- Windows (native and WSL2)

## Requirements

- Linux, macOS, or Windows (native and WSL2).
- Network access for model providers and update checks.
- Everything else (Python runtime, `git`...) is prepared automatically by the installers; only
  source installations have explicit prerequisites (Git and Python 3.11+, Python 3.14 recommended).

## Installation

### Option A: npm (All Platforms — Windows, macOS, Linux)

Nova Agent ships as an npm package that provisions its own Python environment on first run.

```bash
npm install -g @the-sanjeevkumar/nova-agent
```

Then launch:

```bash
nova
```

No Python, Docker, or Windows-only steps required. The first run creates a virtual environment under `~/.nova/venv` and installs dependencies automatically.

### Option B: One-Line Installation (curl — Linux/macOS/WSL2)

```bash
curl -fsSL https://raw.githubusercontent.com/the-sanjeevkumar/nova-agent/main/scripts/install.sh | bash
```

This ensures Node.js is present (installing it via your system package manager if missing), then runs the npm install above.

### Option C: Install from Source (Developers)

```bash
git clone https://github.com/the-sanjeevkumar/nova-agent.git
cd nova-agent
python -m pip install -e .
```

If you use `uv`, install dependencies with your preferred workflow. The project uses `pyproject.toml` for dependency management.

### Installation Comparison

| Method | Recommended For | Requirements |
|---|---|---|
| **npm** | **All platforms (Windows, macOS, Linux)** | Node.js 18+ (auto-installed by the curl script) |
| curl installer | Linux/macOS/WSL2 users | curl |
| Source installation | Developers | Git and Python 3.11+ |

## Configuration

Nova Agent stores configuration and state in the Nova home directory (`~/.nova` by default, or `$NOVA_HOME`).

On first run, Nova starts in **setup mode automatically** — the setup wizard walks you through connecting a provider, picking a model, and configuring your preferences, and exits itself once complete. Subsequent starts go straight to the interactive agent. You can rerun it any time with `nova setup`.

Common locations:

- Configuration: `~/.nova/config.yaml`
- Environment variables: `~/.nova/.env`
- Memory and sessions: `~/.nova/`

Example environment configuration (use placeholders, never real keys):

```bash
OPENAI_API_KEY=your-api-key-here
# or configure any other supported provider
```

Run the setup wizard:

```bash
nova setup
```

Or configure a model directly:

```bash
nova model
```

## Usage

Start Nova Agent:

```bash
nova
```

Common commands:

```bash
nova model        # Choose provider and model
nova tools        # Configure enabled tools
nova config set   # Set a config value
nova config get   # Read a config value
nova gateway      # Start messaging gateway
nova setup        # Full setup wizard
nova update       # Update to latest version
nova doctor       # Diagnose issues
```

## Automatic Updates

Nova Agent checks GitHub for new stable releases and notifies you when an update is available.

- Version comparison uses semantic versioning and is not alphabetical.
- Checks run on startup in the background and do not block the application.
- Network failures are handled gracefully.
- Repeated notifications for the same release are suppressed.
- Manual check: `nova update` or `nova update --check`.
- Disable automatic checks in configuration if desired.
- Updates are never forced; your permission is required.

When an update is available, a notification displays the installed version, latest version, and release notes, with options to update now, later, or view release notes.

### Update Methods

**npm-based installation:**
```bash
npm install -g @the-sanjeevkumar/nova-agent@latest
```
Or simply `nova update`, which refuses in place and prints the npm command above (npm owns the update).

**curl installer (re-run the bootstrap script):**
```bash
curl -fsSL https://raw.githubusercontent.com/the-sanjeevkumar/nova-agent/main/scripts/install.sh | bash
```

**Source installation:**
```bash
cd nova-agent && git pull && python -m pip install -e .
```

User configuration, memory, and local data are preserved during updates.

## Uninstall

Run the uninstaller:

```bash
curl -fsSL https://raw.githubusercontent.com/the-sanjeevkumar/nova-agent/main/uninstall.sh | bash
```

It offers two options:

1. **Temp Uninstall** — removes the Nova code and launchers only; your config, secrets, sessions and logs in `~/.nova` are kept (so a reinstall picks up where you left off).
2. **Complete Uninstall** — removes everything, including all data. You will be asked to type `yes` to confirm.

## Release Notes

Release notes are maintained on GitHub:

https://github.com/the-sanjeevkumar/nova-agent/releases

Each release documents new features, improvements, bug fixes, performance changes, and breaking changes. See `CHANGELOG.md` for a local summary.

## Project Structure

```text
nova-agent/
├── README.md
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
├── package.json
├── Dockerfile
├── install.sh
├── nova_cli/
├── agent/
├── gateway/
├── tools/
├── tests/
└── .github/
    └── workflows/
```

## Troubleshooting

- **Runtime version errors**: ensure Python 3.11+ is installed.
- **Missing dependencies**: run `nova doctor`.
- **API key issues**: verify `~/.nova/.env`.
- **GitHub rate limits**: set `GITHUB_TOKEN` for higher limits (optional).
- **Update failures**: rerun the installer; your data is preserved.
- **Docker issues**: ensure volumes mount `~/.nova`.

## Security and Privacy

- Configuration and memory are stored locally in your Nova home directory.
- Credentials are read from environment/config and are never committed.
- Updates are downloaded from GitHub and verified where checksums are provided.
- Optional network features can be disabled in configuration.
- Nova Agent sends data to model providers you configure; local models are supported by some providers.

## Contributing

Contributions are welcome.

1. Fork the repository.
2. Create a feature branch.
3. Install development dependencies.
4. Run tests.
5. Submit a pull request.

See `CONTRIBUTING.md` for details.

## License

This project is licensed under the MIT License. See `LICENSE`.

