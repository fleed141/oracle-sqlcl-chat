import asyncio
from loguru import logger
from langchain.chat_models import init_chat_model
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.agents import create_agent
from langchain.agents.middleware import (
    SummarizationMiddleware,
    ContextEditingMiddleware,
    ClearToolUsesEdit,
    wrap_tool_call,
)
from langchain.messages import ToolMessage
from deepagents.middleware import FilesystemMiddleware
from deepagents.backends import StoreBackend
from app.skills import SkillMiddleware


@wrap_tool_call
async def handle_tool_errors(request, handler):
    try:
        return await handler(request)
    except Exception as e:
        return ToolMessage(
            content=f"Tool error: Please check your input and try again. ({str(e)})",
            tool_call_id=request.tool_call["id"],
        )


def create_model(settings: dict):
    provider = settings.get("LLM_PROVIDER", "") or settings.get("provider", "")
    model_name = settings.get("LLM_MODEL", "") or settings.get("model", "")
    api_key = settings.get("LLM_API_KEY", "") or settings.get("api_key", "")

    if not model_name:
        raise ValueError("LLM_MODEL is not configured")

    if provider == "openrouter":
        return init_chat_model(model=model_name, model_provider="openrouter")
    else:
        return init_chat_model(model=model_name)


class AgentManager:
    def __init__(self, checkpointer, store):
        self._agent = None
        self._swap_lock = asyncio.Lock()
        self._checkpointer = checkpointer
        self._store = store
        self._session_task: asyncio.Task | None = None
        self._active_requests = 0

    async def start(self, settings: dict):
        self._agent, self._session_task = await self._spawn_session(settings)
        logger.info("Agent started successfully")

    async def get_agent(self):
        self._active_requests += 1
        try:
            return self._agent
        finally:
            self._active_requests -= 1

    async def recreate(self, settings: dict):
        new_agent, new_task = await self._spawn_session(settings)

        old_task = None
        async with self._swap_lock:
            while self._active_requests > 0:
                await asyncio.sleep(0.1)

            old_task = self._session_task
            self._agent = new_agent
            self._session_task = new_task

        if old_task is not None:
            old_task.cancel()
            try:
                await old_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Error cleaning up old session task: {e}")

        logger.success("Agent hot-swapped successfully")

    def _make_agent(self, tools, settings: dict):
        model = create_model(settings)

        system_prompt = settings.get("SYSTEM_PROMPT", "").replace(
            "{LANGUAGE}", settings.get("LANGUAGE", "Spanish")
        )
        filesystem_prompt = settings.get("FILESYSTEM_PROMPT", "")

        return create_agent(
            model=model,
            tools=tools,
            store=self._store,
            system_prompt=system_prompt,
            middleware=[
                handle_tool_errors,
                SummarizationMiddleware(
                    model=model,
                    trigger=("tokens", 20000),
                    keep=("messages", 10),
                ),
                ContextEditingMiddleware(
                    edits=[
                        ClearToolUsesEdit(
                            trigger=20000,
                            keep=5,
                        ),
                    ],
                ),
                FilesystemMiddleware(
                    backend=StoreBackend(
                        namespace=lambda rt: (rt.context["user_id"],)
                    ),
                    system_prompt=filesystem_prompt,
                    tool_token_limit_before_evict=10000,
                    human_message_token_limit_before_evict=5000,
                ),
                SkillMiddleware(),
            ],
            checkpointer=self._checkpointer,
            name="Oracle chat",
        )

    async def _spawn_session(self, settings: dict) -> tuple:
        from app.settings_db import get_active_llm_config

        sqlcl_path = settings.get("SQLCL_PATH", "/workspace/sqlcl_files/bin/sql")
        agent_future: asyncio.Future = asyncio.Future()

        llm_config = await get_active_llm_config()
        if llm_config:
            settings = {**settings, **llm_config}

        async def _session_runner():
            mcp_client = MultiServerMCPClient(
                {
                    "sqlcl": {
                        "command": sqlcl_path,
                        "args": ["-mcp"],
                        "transport": "stdio",
                    }
                }
            )

            async with mcp_client.session("sqlcl") as session:
                tools = await load_mcp_tools(session)
                logger.info(f"Tools loaded from sqlcl: {[t.name for t in tools]}")

                agent = self._make_agent(tools, settings)
                if not agent_future.done():
                    agent_future.set_result(agent)

                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass

        task = asyncio.create_task(_session_runner())
        agent = await agent_future
        return agent, task

    async def shutdown(self):
        if self._session_task is not None:
            self._session_task.cancel()
            try:
                await self._session_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Error during shutdown: {e}")
