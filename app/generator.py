import json
from loguru import logger
from langgraph.graph.state import CompiledStateGraph
from app.models import UserMessage
from langsmith import traceable


@traceable
async def stream_agent_response(agent: CompiledStateGraph, mensaje: UserMessage):
    logger.info(f"Mensaje recibido: {mensaje.message}")
    logger.info("Iniciando stream de respuesta...")
    yield f"data: {json.dumps({'type': 'info', 'content': 'Conectado. Analizando petición...'})}\n\n"

    try:

        async for chunk, metadata in agent.astream(
            input={"messages": [{"role": "user", "content": mensaje.message}]},
            config={
                "configurable": {"thread_id": f"{mensaje.user_id}:{mensaje.thread_id}"}
            },
            stream_mode="messages",
            context={"user_id": mensaje.user_id},
        ):

            # Ignoramos cualquier fragmento que provenga del proceso de resumen
            if metadata and metadata.get("lc_source") == "summarization":
                continue

            if (
                hasattr(chunk, "additional_kwargs")
                and chunk.additional_kwargs.get("lc_source") == "summarization"
            ):
                continue
            # ==========================================

            texto_chunk = ""

            # Extracción de texto
            if hasattr(chunk, "content") and chunk.content:
                if isinstance(chunk.content, str):
                    texto_chunk = chunk.content
                elif isinstance(chunk.content, list) and len(chunk.content) > 0:
                    item = chunk.content[0]
                    if isinstance(item, dict) and "text" in item:
                        texto_chunk = item["text"]
                    elif hasattr(item, "text"):
                        texto_chunk = item.text

            es_mensaje_herramienta = (
                getattr(chunk, "type", "") == "tool"
                or "ToolMessage" in chunk.__class__.__name__
            )

            if es_mensaje_herramienta:
                if texto_chunk:
                    yield f"data: {json.dumps({'type': 'tool_result', 'content': texto_chunk})}\n\n"
            else:
                # Enviar texto de la IA
                if texto_chunk:
                    yield f"data: {json.dumps({'type': 'text', 'content': texto_chunk})}\n\n"

                # Notificar uso de herramientas
                if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    nombres_herramientas = [
                        (
                            tc.get("name")
                            if isinstance(tc, dict)
                            else getattr(tc, "name", "BD")
                        )
                        for tc in chunk.tool_calls
                    ]
                    yield f"data: {json.dumps({'type': 'tool', 'tools': nombres_herramientas, 'content': 'Consultando base de datos...'})}\n\n"

    except Exception as e:
        logger.exception(f"Error en el stream: {str(e)}")
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    yield f"data: {json.dumps({'type': 'done', 'content': 'Terminado'})}\n\n"
    logger.success(
        f"Stream finalizado para usuario {mensaje.user_id} en thread {mensaje.thread_id}"
    )


@traceable
async def agent_response(agent: CompiledStateGraph, mensaje: UserMessage):
    logger.info("Procesando respuesta del agente...")
    try:
        response = await agent.ainvoke(
            input={"messages": [{"role": "user", "content": mensaje.message}]},
            config={
                "configurable": {"thread_id": f"{mensaje.user_id}:{mensaje.thread_id}"}
            },
            context={"user_id": mensaje.user_id},
        )

        logger.success(f"Respuesta generada para el usuario {mensaje.user_id}")

        if not response.get("messages"):
            raise ValueError("El agente no generó ningún mensaje de respuesta")

        last_message = response["messages"][-1]
        if isinstance(last_message.content, list):
            return (
                last_message.content[0].get("text", last_message.content)
                if last_message.content
                else ""
            )
        else:
            return last_message.content
    except Exception as e:
        logger.exception(f"Error procesando respuesta del agente: {str(e)}")
        raise
