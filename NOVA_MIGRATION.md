# Nova migration and compatibility notes

Nova is a local rebrand of the Hermes Agent codebase, created by Nova Labs. The original agent loop, providers, tools, memory, skills, extensions, gateway, and UI implementations remain in place.

- The Python distribution stays `hermes-agent`; its optional dependency groups and existing environments refer to that distribution name.
- Python import modules such as `nova_cli`, `nova_constants`, and `nova_state` remain stable for internal imports and installed plugins.
- `nova` is the primary launcher. `hermes` and `hermes-agent` remain compatibility launchers.
- The configuration home stays `~/.hermes` (or `HERMES_HOME`). This preserves existing credentials, configuration, profiles, conversation history, skills, and memory.
- A user-authored `SOUL.md` is left intact. Known generated default persona templates are upgraded to Nova's identity when the CLI initializes the profile.
- Existing provider authentication and subscription-management integrations remain available where explicitly configured. Nova does not provide credentials or bypass provider access requirements. Subscription management commands are user-invoked; they are not part of the default startup flow.
- The source checkout has no Nova Labs release service configured. The inherited Hermes updater cannot provide Nova releases until a Nova update source is established.
- `LICENSE` and upstream third-party notices are retained.
