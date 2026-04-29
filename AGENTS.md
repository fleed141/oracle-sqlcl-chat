# AGENTS.md

## Entrypoint & commands

```bash
# Dev run (single worker, reload on change)
fastapi run app/main.py --reload

# Docker build & run
docker compose up -d
docker compose logs -f
```

The app is started via `fastapi run`, not `uvicorn` directly. `API_WORKERS` controls workers.

## Architecture

```
app/
  main.py           FastAPI app + lifespan + /api/v1/chat/ endpoints
  agent_manager.py  AgentManager — builds agent, hot-swap via asyncio.Task
  generator.py      SSE streaming + sync response generators
  config.py         Env settings (pydantic-settings), SQLcl download/bootstrap
  database.py       SQLAlchemy async engine (sqlite+aiosqlite)
  settings_db.py    ORM models + CRUD for agent_settings + connections
  models.py         Pydantic schemas (UserMessage only)
  skills.py         SkillMiddleware — injects skills/SKILLS.md into system prompt
  web.py            NiceGUI chat page at /gui, settings page at /gui/settings
skills/             Oracle DB reference guides (indexed by SKILLS.md)
memory/             Runtime SQLite files (auto-created)
```

## Dual SQLite databases

There are **two separate SQLite systems** — do not merge them:

| File | Managed by | Purpose |
|------|-----------|---------|
| `memory/settings.sqlite` | SQLAlchemy (`app/database.py`) | Agent config, connections, editable from UI |
| `memory/checkpoints.sqlite` | LangGraph `AsyncSqliteSaver` | Conversation thread history |
| `memory/store.sqlite` | LangGraph `AsyncSqliteStore` | Long-term memory store |

The LangGraph ones are framework-internal and must not be touched directly.

## MCP session: cross-task cancel scope hazard

The `MultiServerMCPClient.session()` uses `anyio` cancel scopes internally. These are **tied to the asyncio task** that entered the context manager. Doing `__aexit__` from a different task will crash with `"Attempted to exit cancel scope in a different task than it was entered in"`.

**What the code does:** The session runs inside a dedicated `asyncio.Task` (`_session_runner`). When recreating, the old task is `.cancel()`-ed, which causes `__aexit__` to run from **within the same task**. Never call `__aexit__` manually across tasks.

## Agent hot-swap flow

1. NiceGUI settings page calls `set_setting()` / `add_connection()` directly (same process, no HTTP)
2. Calls `agent_manager.recreate(new_settings)`
3. `recreate()` builds new agent in a new task, locks, waits for in-flight requests to drain, swaps `_agent` reference, then cancels the old session task
4. `get_agent()` tracks `_active_requests` so the swap doesn't interrupt active conversations

## Settings lifecycle

1. On first boot, `seed_defaults()` copies env vars → `agent_settings` table (only if key doesn't exist)
2. Env connections are also seeded into the `connections` table
3. The UI at `/gui/settings` reads/writes directly to SQLite via `settings_db` functions
4. After save, `recreate()` rebuilds the agent and reinitializes SQLcl connections via `sqlcl_init_config()`

Settings keys stored in DB: `SYSTEM_PROMPT`, `FILESYSTEM_PROMPT`, `LANGUAGE`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `SQLCL_PATH`.

The `{LANGUAGE}` placeholder in `SYSTEM_PROMPT` is replaced at agent build time via `.replace("{LANGUAGE}", settings["LANGUAGE"])`.

## Connection string format

Connections in `.env` use: `[name,user/password@host:port/servicename][name2,...]`

`parse_connection_string()` regex-parses this. In the DB, connections are normalized into rows with `name`, `connection_string`, `status`, and `status_message` columns.

## Connection discovery & testing at startup

`sync_sqlcl_connections()` in `settings_db.py` runs at startup after saving DB connections into SQLcl. It:
1. Runs `connmgr list` via SQLcl to discover all saved connections
2. For undiscovered names, runs `connmgr show <name>` to extract the connection string and inserts into DB
3. Tests each connection via `connmgr test <name>` and writes status (`ok`/`failed`) to the `connections` table

SQLcl interaction uses `subprocess.Popen` with `/NOLOG` + stdin commands — same pattern as `sqlcl_init_config()`. The helper `_sqlcl_exec()` handles the common pattern. Functions in `config.py`: `sqlcl_list_connections`, `sqlcl_show_connection`, `sqlcl_test_connection`.

## SQLcl auto-download

`check_for_sqlcl()` in `config.py` downloads `sqlcl-latest.zip` from Oracle if the binary at `SQLCL_PATH` is missing. It extracts to `sqlcl_files/` and does `chmod +x` on the `sql` script.

## Skills directory

`skills/SKILLS.md` is the index loaded by `SkillMiddleware`. Each `.md` file under `skills/` is a reference guide the agent can load via the `load_skill` tool. The directory is mounted as a volume in Docker and can be replaced with custom skills.

## Key dependencies

- `langchain_mcp_adapters` bridges LangChain to SQLcl MCP over stdio
- `deepagents` provides `FilesystemMiddleware` + `StoreBackend`
- `nicegui` mounts on FastAPI via `ui.run_with(app=app, mount_path="/gui")`
- `loguru` for logging (not stdlib `logging`)
- `sqlalchemy[asyncio]` + `aiosqlite` + `greenlet` for async SQLite ORM

## No tests

There are no tests in this repo. Any refactoring must be verified manually by running the dev server.

## Common pitfalls

- `SystemPrompt` and `FILESYSTEM_PROMPT` are **required** env vars (no default in `Settings` class)
- `NICEGUI_STORAGE_SECRET` is required for NiceGUI's browser storage
- The `UserMessage` model has exactly 3 fields: `message`, `user_id`, `thread_id` — no `preferences` field
- `LangGraph` thread IDs are namespaced as `{user_id}:{thread_id}` to isolate per-user conversations
- The `FilesystemMiddleware` uses `namespace=lambda rt: (rt.context["user_id"],)` — file storage is per-user
