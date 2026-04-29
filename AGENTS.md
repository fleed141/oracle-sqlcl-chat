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
  settings_db.py    ORM models + CRUD for agent_settings + connections + llm_configs
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
| `memory/settings.sqlite` | SQLAlchemy (`app/database.py`) | Agent config, connections, LLM providers, editable from UI |
| `memory/checkpoints.sqlite` | LangGraph `AsyncSqliteSaver` | Conversation thread history |
| `memory/store.sqlite` | LangGraph `AsyncSqliteStore` | Long-term memory store |

The LangGraph ones are framework-internal and must not be touched directly.

## SQLAlchemy schema migration

`database.py:init_db()` does `Base.metadata.create_all` for new tables, plus runs a manual migration via `exec_driver_sql("PRAGMA table_info...")` to add columns (`status`, `status_message`) to existing `connections` tables. New tables (`llm_configs`) are created automatically.

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
3. **Every boot**: LLM providers from `.env` are synced to `llm_configs` table (upsert by provider name, remove stale ones). First-ever boot activates the first provider found.
4. The UI at `/gui/settings` reads/writes directly to SQLite via `settings_db` functions
5. After save, `recreate()` rebuilds the agent and reinitializes SQLcl connections via `sqlcl_init_config()`

Settings keys stored in DB: `SYSTEM_PROMPT`, `FILESYSTEM_PROMPT`, `LANGUAGE`, `SQLCL_PATH`. LLM config is now stored in the `llm_configs` table (see below).

The `{LANGUAGE}` placeholder in `SYSTEM_PROMPT` is replaced at agent build time via `.replace("{LANGUAGE}", settings["LANGUAGE"])`.

## LLM provider configuration

Multiple providers can be defined in `.env` (Google, OpenAI, Anthropic, DeepSeek, OpenRouter). Each needs both `*_API_KEY` and `*_MODEL`. On startup, `_detect_all_llm_configs()` scans all env vars and syncs them into the `llm_configs` table:

```sql
llm_configs (id, label, provider, model, api_key, is_active)
```

The `/gui/settings` page shows a dropdown of available providers (read-only, sourced from env). Selecting one and saving marks it as `is_active` and recreates the agent. The active config's `provider`/`model`/`api_key` are passed to `init_chat_model()` at agent build time.

## Connection string format

Connections in `.env` use: `[name,user/password@host:port/servicename][name2,...]`

`parse_connection_string()` regex-parses this. In the DB, connections are normalized into rows:

```sql
connections (id, name, connection_string, status, status_message)
```

## Connection discovery & testing at startup

`sync_sqlcl_connections()` runs as a **background task** (`asyncio.create_task`) after the server is ready. It:

1. Runs `connmgr list -flat` via SQLcl to discover all saved connections
2. Filters banner junk lines (keywords: `release`, `production`, `copyright`, `all rights reserved`, `sqlcl:`)
3. For undiscovered names, runs `connmgr show <name>` to extract `Connect String:` and `User:`; stores as `user@host:port/service` (password is masked by SQLcl)
4. Tests each connection via `connmgr test <name>`, writes `ok`/`failed` to the `connections` table

The UI at `/gui/settings` shows connection status with color-coded icons and a manual test button per connection.

## SQLcl interaction pattern

All SQLcl commands use `subprocess.Popen` with `/NOLOG` + stdin commands. The helper `_sqlcl_exec()` in `config.py` runs commands prefixed with `set feedback off` to suppress banners.

| Function | SQLcl command | Output parsing |
|----------|--------------|----------------|
| `sqlcl_list_connections` | `connmgr list -flat` | One name per line, filter banner junk |
| `sqlcl_show_connection` | `connmgr show <name>` | Parses `Connect String:` and `User:` lines |
| `sqlcl_test_connection` | `connmgr test <name>` | Checks for `"Connection Test Successful"` |
| `sqlcl_init_config` | `conn -sv -save <name> <string>` | Saves with secure vault (`-sv`). Can take up to 15s timeout if host unreachable. Treats `"already exists"` and `"Connection failed"` as non-errors. |

The `SQL>` prompt line is NOT part of the output — only user input, not returned by SQLcl.

## SQLcl auto-download

`check_for_sqlcl()` in `config.py` downloads `sqlcl-latest.zip` from Oracle if the binary at `SQLCL_PATH` is missing. It extracts to `sqlcl_files/` and does `chmod +x` on the `sql` script.

## Skills directory

`skills/SKILLS.md` is the index loaded by `SkillMiddleware`. Each `.md` file under `skills/` is a reference guide the agent can load via the `load_skill` tool. The directory is mounted as a volume in Docker and can be replaced with custom skills.

## SkillMiddleware caching

`SkillMiddleware` loads the skill index once (lazy, with `asyncio.Lock` for thread safety). The index is cached in `_skills_prompt` and reused across calls.

## UI patterns (NiceGUI)

- **`ui.notify` must be wrapped in `try/except RuntimeError`** — if the user navigates away from a page while an async operation completes, the parent slot is deleted and `notify()` fails.
- **Long operations** (subprocess, agent recreate) should run via `asyncio.to_thread()` and disable the triggering button with `props("loading")`, restored in `finally`.
- **`on_change`** callbacks receive `ValueChangeEventArguments`, not the raw value. Use `lambda e: handler(e.value)`.
- **Function definitions** inside `with ui.column()` / `with ui.card()` must be at a deeper indentation than the `with` statement, otherwise they close the context.

## Key dependencies

- `langchain_mcp_adapters` bridges LangChain to SQLcl MCP over stdio
- `deepagents` provides `FilesystemMiddleware` + `StoreBackend`
- `nicegui` mounts on FastAPI via `ui.run_with(app=app, mount_path="/gui")`
- `loguru` for logging (not stdlib `logging`)
- `sqlalchemy` + `aiosqlite` + `greenlet` for async SQLite ORM

## No tests

There are no tests in this repo. Any refactoring must be verified manually by running the dev server.

## Common pitfalls

- `SystemPrompt` and `FILESYSTEM_PROMPT` are **required** env vars (no default in `Settings` class)
- `NICEGUI_STORAGE_SECRET` is required for NiceGUI's browser storage
- The `UserMessage` model has exactly 3 fields: `message`, `user_id`, `thread_id` — no `preferences` field
- `LangGraph` thread IDs are namespaced as `{user_id}:{thread_id}` to isolate per-user conversations
- The `FilesystemMiddleware` uses `namespace=lambda rt: (rt.context["user_id"],)` — file storage is per-user
- Do NOT call `__aexit__` on MCP session context managers across asyncio tasks
- Always wrap `ui.notify()` in `try/except RuntimeError` for async operations that outlive the page