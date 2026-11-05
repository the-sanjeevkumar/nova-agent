---
name: self-install
description: Install a plugin, skill, or MCP server into Nova when the user asks. Use when the user gives a GitHub repo/URL or says "install this into yourself".
---

# Self-Install Capability

When the user asks you to install a GitHub repository, plugin, skill, or MCP server into yourself, you CAN do it using Nova's own CLI. Always confirm with the user before making changes, and report what was installed and whether a restart is needed.

## Procedure

1. **Identify the type**:
   - Plugin catalog entry: `nova plugins install <name>` (then `nova plugins enable <name>`)
   - GitHub repo / owner/repo: `nova plugins install <owner>/<repo>` or full URL
   - Skill: `nova skills install <source>` (GitHub, skills.sh, ClawHub, or local path)
   - MCP server: `nova mcp add <name> <command>` or edit config

2. **Run the install command** via the terminal tool. Capture output.

3. **Enable if needed**: `nova plugins enable <name>`.

4. **Report**: what installed, location, any dependency conflicts, and whether `nova gateway restart` is required.

5. **Safety**: Never install from untrusted sources without user confirmation. For plugins that require env vars (API keys), tell the user which vars to set in `~/.hermes/.env`.

## Examples

- User: "install https://github.com/foo/bar into yourself"
  → Run `nova plugins install https://github.com/foo/bar`, then enable.

- User: "add the gmail skill"
  → Run `nova skills install gmail` (or `nova plugins install gmail` if it's a plugin).

## Notes

- Plugins install into `~/.hermes/plugins/` and are profile-scoped.
- Some plugins conflict with core dependencies; if install fails with a resolver error, report it and suggest alternatives.
- After installing gateway plugins, restart the gateway: `nova gateway restart`.
