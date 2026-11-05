# Nova CLI Reference

Live sources when anything looks stale: `nova --help`, `nova <command> --help`,
https://hermes-agent.nousresearch.com/docs/reference/cli-commands

### Global Flags

```
nova [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
nova chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
nova setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
nova model                Interactive model/provider picker
nova fallback [add|remove|list]  Fallback provider chain
nova config [show|edit|get|set|unset|path|env-path|check|migrate]
nova login / logout       OAuth sign-in / clear stored auth
nova doctor [--fix]       Check dependencies and config
nova status [--full]      Component summary (--full: every section)
```

### Tools & Skills

```
nova tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

nova skills list|browse|search QUERY|inspect ID
nova skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
nova skills config        Enable/disable skills per platform
nova skills check|update|uninstall|publish PATH
nova skills tap add REPO  Add a GitHub repo as a skill source
nova bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
nova mcp add NAME (--url or --command) | remove | list | test NAME
nova mcp catalog | install NAME     Curated catalog install
nova mcp configure NAME             Toggle tool selection
nova mcp-serve                      Run Nova as an MCP server (mcp-serve plugin)
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
nova gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `nova photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/

### Sessions

```
nova sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
nova cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
nova webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
nova profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
nova profile rename A B | alias NAME | export NAME | import FILE
nova profile migrate-identity A B   Retry a completed rename's session/routing identity migration
```

### Credentials & Pools

```
nova auth                 Interactive credential manager
nova auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
nova auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
nova desktop / gui        Native desktop app
nova dashboard            Web admin panel + embedded chat (--stop / --status)
nova proxy                OpenAI-compatible local proxy backed by an OAuth provider
nova portal               Quick setup / sign in via Nous Portal
nova kanban <verb>        Multi-agent work-queue board
nova project              Named multi-folder workspaces
nova skin list|use|set    Switch/tweak skins (see references/themes.md)
nova pets <verb>          Pet mascots (see references/petdex.md)
nova memory setup|status|off|reset   Memory provider
nova secrets bitwarden|onepassword   External secret stores
nova moa                  Mixture-of-Agents slots
nova hooks / security / backup / import / checkpoints / console
nova logs [-f] [errors]   View agent/error logs
nova send                 One-off message through a gateway platform
nova pairing / plugins / insights / journey / computer-use
nova acp                  ACP server (IDE integration)
nova completion bash|zsh|fish
nova update / uninstall
```

Plugin- and provider-supplied subcommands (e.g. `nova photon setup`) only appear once their plugin is installed/active. Importing from OpenClaw, Claude Code, Codex CLI or Cursor (`nova import-agent <agent>`, plus the older `nova claw migrate|cleanup`) comes from the `agent-importers` plugin; a fresh `nova setup` that finds one of those agents offers to install it.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `nova config edit` · [Configuration docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| Tools / toolsets | `nova tools list` · [Tools reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference) |
| Skills catalog | `nova skills browse` · [Skills catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) |
| Provider setup | `nova model` · [Providers guide](https://hermes-agent.nousresearch.com/docs/integrations/providers) |
| Env variables | `nova config env-path` · [Env vars reference](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) |
| Gateway logs | `~/.hermes/logs/gateway.log` (or `nova logs`) |
| Sessions | `nova sessions browse` (reads state.db) |
