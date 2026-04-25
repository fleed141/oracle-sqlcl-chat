from app.web import init_nicegui
from nicegui import ui
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.agents import create_agent
from loguru import logger
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite import AsyncSqliteStore
from langchain.chat_models import init_chat_model
from langchain.messages import ToolMessage
from contextlib import asynccontextmanager
from app.config import parse_connection_string, settings, parse_connection_string
import os
from deepagents.middleware import FilesystemMiddleware
from langchain.agents.middleware import (
    SummarizationMiddleware,
    ContextEditingMiddleware,
    ClearToolUsesEdit,
    wrap_tool_call,
)

from deepagents.backends import StoreBackend
from langchain.agents import create_agent
from app.generator import stream_agent_response, agent_response
from app.models import UserMessage
from app.config import sqlcl_init_config, check_for_sqlcl
from app.skills import SkillMiddleware

if settings.GOOGLE_API_KEY:
    if not settings.GOOGLE_GEMINI_MODEL:
        logger.error(
            "Se ha configurado GOOGLE_API_KEY pero no GOOGLE_GEMINI_MODEL. Por favor, configure ambos para usar el modelo de Google."
        )
        raise Exception("Configuración incompleta para Google Gemini")

    model = init_chat_model(model=settings.GOOGLE_GEMINI_MODEL)
elif settings.DEEPSEEK_API_KEY:
    if not settings.DEEPSEEK_MODEL:
        logger.error(
            "Se ha configurado DEEPSEEK_API_KEY pero no DEEPSEEK_MODEL. Por favor, configure ambos para usar el modelo de Deepseek."
        )
        raise Exception("Configuración incompleta para Deepseek")

    model = init_chat_model(model=settings.DEEPSEEK_MODEL)
elif settings.OPENROUTER_API_KEY:
    if not settings.OPENROUTER_MODEL:
        logger.error(
            "Se ha configurado OPENROUTER_API_KEY pero no OPENROUTER_MODEL. Por favor, configure ambos para usar el modelo de OpenRouter."
        )
        raise Exception("Configuración incompleta para OpenRouter")

    model = init_chat_model(
        model=settings.OPENROUTER_MODEL, model_provider="openrouter"
    )
elif settings.ANTHROPIC_API_KEY:
    if not settings.ANTHROPIC_MODEL:
        logger.error(
            "Se ha configurado ANTHROPIC_API_KEY pero no ANTHROPIC_MODEL. Por favor, configure ambos para usar el modelo de Anthropic."
        )
        raise Exception("Configuración incompleta para Anthropic")

    model = init_chat_model(model=settings.ANTHROPIC_MODEL)
elif settings.OPENAI_API_KEY:
    if not settings.OPENAI_MODEL:
        logger.error(
            "Se ha configurado OPENAI_API_KEY pero no OPENAI_MODEL. Por favor, configure ambos para usar el modelo de OpenAI."
        )
        raise Exception("Configuración incompleta para OpenAI")

    model = init_chat_model(model=settings.OPENAI_MODEL)
else:
    logger.error(
        "No se ha configurado ninguna clave de API para los modelos. Por favor, configure al menos una"
    )
    raise


@wrap_tool_call
async def handle_tool_errors(request, handler):
    try:
        return await handler(request)
    except Exception as e:
        return ToolMessage(
            content=f"Tool error: Please check your input and try again. ({str(e)})",
            tool_call_id=request.tool_call["id"],
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Encendiendo...")
        os.makedirs("memory", exist_ok=True)

        # async with AsyncPostgresSaver.from_conn_string(settings.DB_URI) as checkpointer:
        async with AsyncSqliteSaver.from_conn_string(
            "./memory/checkpoints.sqlite"
        ) as checkpointer:
            await checkpointer.setup()

            async with AsyncSqliteStore.from_conn_string(
                "./memory/store.sqlite"
            ) as store:
                await store.setup()
                check_for_sqlcl()
                conexiones = parse_connection_string(settings.SQLCL_CONNECTIONS)
                for conn in conexiones:
                    sqlcl_init_config(
                        nombre_conexion=conn["nombre"], cadena_conexion=conn["cadena"]
                    )

                mcp_client = MultiServerMCPClient(
                    {
                        "sqlcl": {
                            "command": settings.SQLCL_PATH,
                            "args": ["-mcp"],
                            "transport": "stdio",
                        }
                    }
                )

                async with mcp_client.session("sqlcl") as mcp_session:

                    logger.info(f"Prompt general cargado: {settings.SYSTEM_PROMPT}")

                    logger.info(
                        f"Prompt para sistema de archivos cargado: {settings.FILESYSTEM_PROMPT}"
                    )

                    tools = await load_mcp_tools(mcp_session)
                    # tools = await mcp_client.get_tools()
                    logger.info(
                        f"Herramientas cargadas desde sqlcl: {[t.name for t in tools]}"
                    )
                    agent = create_agent(
                        model=model,
                        tools=tools,
                        store=store,
                        system_prompt=settings.SYSTEM_PROMPT,
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
                                system_prompt=settings.FILESYSTEM_PROMPT,
                                tool_token_limit_before_evict=10000,
                                human_message_token_limit_before_evict=5000,
                            ),
                            SkillMiddleware(),
                        ],
                        checkpointer=checkpointer,
                        name="Oracle chat",
                    )

                    app.state.agent = agent

                    logger.info("API lista. Escuchando peticiones...")
                    yield

        logger.info("Apagando servidores y cerrando conexiones limpiamente...")
    except Exception as e:
        logger.exception(f"Error durante el lifespan: {str(e)}")


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Endpoint normal (Síncrono para el cliente HTTP)
@app.post("/api/v1/chat/")
async def recibir_mensaje(request: Request, mensaje: UserMessage):
    agent = request.app.state.agent
    try:
        return agent_response(agent, mensaje)

    except Exception as e:
        logger.exception(f"Error procesando petición: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error en el motor del agente: {str(e)}"
        )


@app.post("/api/v1/chat-stream/")
async def recibir_mensaje_stream(request: Request, mensaje: UserMessage):
    agent = request.app.state.agent

    logger.info(
        f"Recibida petición de usuario {mensaje.user_id} en thread {mensaje.thread_id}, mensaje: {mensaje.message}"
    )

    return StreamingResponse(
        stream_agent_response(agent, mensaje), media_type="text/event-stream"
    )


init_nicegui(app)
ui.run_with(
    app=app,
    title="Oracle CHAT",
    storage_secret=settings.NICEGUI_STORAGE_SECRET,
    mount_path="/gui",
)
