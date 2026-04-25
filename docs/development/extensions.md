# IDE Integrations & Plugins

The Maxwell Daemon acts as the control tower for AI agents. To provide a seamless development experience, we support first-party plugins for major IDEs and text editors. These plugins communicate with the Daemon over its REST and WebSocket APIs.

## Supported Extensions

Foundational plugin structures have been scaffolded for the following platforms (Issue #493):

- **VS Code** (`extensions/vscode`): The primary IDE integration offering inline agent suggestions, task management, and artifact review.
- **JetBrains** (`extensions/jetbrains`): Support for IntelliJ IDEA, PyCharm, and WebStorm, utilizing the JetBrains Plugin SDK.
- **Zed** (`extensions/zed`): Native integration for the Zed editor, prioritizing speed and low-latency agent interactions.
- **Obsidian** (`extensions/obsidian`): A knowledge-management integration that allows agents to build and refine documentation graphs.

## Extension Architecture

Each extension follows a standard architecture pattern to interface with the Maxwell Daemon:

1. **Authentication & Discovery**: Plugins discover the local or remote daemon (via `config.toml` or `fleet.yaml` manifest) and authenticate.
2. **Event Streaming**: Plugins subscribe to `GET /api/v1/events` (Server-Sent Events) or `WS /api/v1/ws` to receive real-time updates on task statuses, required approvals, and new artifacts.
3. **Action & Task Dispatch**: Users can submit new tasks (`POST /api/v1/tasks`) or review and approve agent actions directly from their editor.

## Development Standard

All extensions reside within the `extensions/` directory. They should be built as thin clients that delegate all heavy cognitive processing and LLM interaction to the Maxwell Daemon backend. 

* **State**: Do not store complex state in the IDE plugin. Treat the daemon's API as the single source of truth for task progress and ledger history.
* **UI Matching**: Extensions should map closely to the concepts shown in the canonical browser UI (`maxwell_daemon/api/ui/`), adopting similar task graphs and approval interfaces.
