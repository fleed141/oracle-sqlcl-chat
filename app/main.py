from app.web import init_nicegui
from nicegui import ui
from loguru import logger
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite import AsyncSqliteStore
from contextlib import asynccontextmanager
from app.config import settings, sqlcl_init_config, check_for_sqlcl, sqlcl_list_connections
import asyncio
import os
from app.generator import stream_agent_response, agent_response
from app.models import UserMessage
from app.agent_manager import AgentManager
from app.settings_db import (
    init_db,
    get_all_settings,
    get_connections,
    seed_defaults,
    sync_sqlcl_connections,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Encendiendo...")
        os.makedirs("memory", exist_ok=True)
        await init_db()
        await seed_defaults(settings)

        async with AsyncSqliteSaver.from_conn_string(
            "./memory/checkpoints.sqlite"
        ) as checkpointer:
            await checkpointer.setup()

            async with AsyncSqliteStore.from_conn_string(
                "./memory/store.sqlite"
            ) as store:
                await store.setup()
                check_for_sqlcl()

                db_connections = await get_connections()
                existing_names = set(
                    await asyncio.to_thread(sqlcl_list_connections)
                )
                for conn in db_connections:
                    if conn["name"] not in existing_names:
                        sqlcl_init_config(
                            nombre_conexion=conn["name"],
                            cadena_conexion=conn["connection_string"],
                        )

                db_settings = await get_all_settings()
                app.state.agent_manager = AgentManager(checkpointer, store)
                await app.state.agent_manager.start(db_settings)

                asyncio.create_task(sync_sqlcl_connections())
                logger.info("API lista. Escuchando peticiones...")
                yield

        await app.state.agent_manager.shutdown()
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


async def _get_agent(request: Request):
    return await request.app.state.agent_manager.get_agent()


@app.post("/api/v1/chat/")
async def recibir_mensaje(request: Request, mensaje: UserMessage):
    agent = await _get_agent(request)
    try:
        return agent_response(agent, mensaje)

    except Exception as e:
        logger.exception(f"Error procesando petición: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error en el motor del agente: {str(e)}"
        )


@app.post("/api/v1/chat-stream/")
async def recibir_mensaje_stream(request: Request, mensaje: UserMessage):
    agent = await _get_agent(request)

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
