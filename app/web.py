from loguru import logger
from nicegui import ui, app
from app.models import UserMessage
import asyncio
import uuid
from app.generator import stream_agent_response
import json


def init_nicegui(fastapi_state):

    @ui.page("/")
    def main_page():

        # 1. Generar o recuperar ID de usuario único
        if "user_id" not in app.storage.user:
            app.storage.user["user_id"] = uuid.uuid4().hex

        # 2. Generar o recuperar historial de hilos
        if "threads" not in app.storage.user:
            app.storage.user["threads"] = ["First Conversation"]

        if "current_thread" not in app.storage.user:
            app.storage.user["current_thread"] = "First Conversation"

        # 3. Estado volátil
        local_state = {"is_processing": False}

        # Fondo de la página neutro y elegante
        ui.query("body").style("background-color: #f8fafc; color: #1e293b;")

        # --- CAMBIO: Se eliminó 'max-w-4xl mx-auto' para ocupar todo el ancho ---
        with ui.column().classes("w-full h-screen p-4 flex flex-col"):
            with ui.card().classes(
                "w-full flex-grow flex flex-col p-0 overflow-hidden shadow-xl rounded-2xl border border-slate-200"
            ):

                # --- CABECERA ---
                with ui.row().classes(
                    "w-full px-6 py-4 bg-slate-900 text-white items-center gap-4 shadow-md z-10"
                ):
                    with ui.row().classes("items-center gap-2 mr-auto"):
                        ui.icon("smart_toy", size="sm").classes("text-blue-400")
                        ui.label("Oracle CHAT").classes(
                            "text-xl font-bold tracking-wide"
                        )

                    thread_selector = (
                        ui.select(
                            options=app.storage.user["threads"],
                            value=app.storage.user["current_thread"],
                            on_change=lambda e: switch_thread(e.value),
                        )
                        .classes(
                            "w-48 bg-slate-800 text-white rounded outline-none border-none"
                        )
                        .props("dense dark standout")
                    )

                    ui.button(icon="add", on_click=lambda: create_new_thread()).props(
                        "flat round color=white"
                    ).tooltip("Nueva conversación")

                # --- ÁREA DE CHAT ---
                scroll_area = ui.scroll_area().classes(
                    "w-full flex-grow p-6 bg-slate-50"
                )
                with scroll_area:
                    chat_box = ui.column().classes("w-full gap-6 pb-4")

            # --- ÁREA DE INPUT ---
            with ui.row().classes("w-full pt-4 gap-3 items-end"):
                msg_input = (
                    ui.input(placeholder="Pregúntale al oraculo ...")
                    .classes("flex-grow text-lg bg-white shadow-sm")
                    .props("rounded outlined clearable autogrow")
                )
                send_btn = ui.button(icon="send").props(
                    "round color=primary size=lg shadow-md"
                )

                # --- FUNCIONES LÓGICAS ---
                async def create_new_thread():
                    new_id = f"Sesión {uuid.uuid4().hex[:4].upper()}"
                    app.storage.user["threads"].append(new_id)
                    app.storage.user["current_thread"] = new_id
                    thread_selector.set_options(app.storage.user["threads"])
                    thread_selector.value = new_id
                    ui.notify(f"Nueva sesión: {new_id}", type="positive")

                def switch_thread(new_thread_id):
                    app.storage.user["current_thread"] = new_thread_id
                    chat_box.clear()

                async def send():
                    if local_state["is_processing"]:
                        return

                    texto = msg_input.value.strip() if msg_input.value else ""
                    if not texto:
                        return

                    # Bloquear UI
                    local_state["is_processing"] = True
                    msg_input.disable()
                    send_btn.disable()
                    msg_input.value = ""

                    with chat_box:
                        ui.chat_message(texto, name="Tú", sent=True).props(
                            'bg-color="blue-6" text-color="white"'
                        ).classes("w-full text-base")

                        # Aparece la burbuja del bot de inmediato
                        bot_container = (
                            ui.chat_message(name="Oracle", sent=False)
                            .props('bg-color="grey-2" text-color="grey-10"')
                            .classes("w-full text-base")
                        )

                        with bot_container:
                            # --- LA CLAVE AQUÍ: Envolvemos todo en un solo DIV ---
                            # Esto fuerza a Quasar a dibujar una sola burbuja sin divisiones
                            with ui.element("div").classes("flex flex-col w-full"):

                                # 1. Contenedor temporal (Animado, visible por defecto)
                                tool_ui_container = ui.row().classes(
                                    "items-center gap-2 text-indigo-600 font-medium py-1 animate-pulse"
                                )
                                with tool_ui_container:
                                    ui.icon("settings", size="xs").classes(
                                        "animate-spin"
                                    )
                                    tool_label = ui.label("Iniciando análisis...")

                                # 2. Contenedor de respuesta final (Oculto al principio)
                                response_label = ui.markdown("").classes("hidden")

                    scroll_area.scroll_to(percent=1.0)

                    try:
                        usuario_obj = UserMessage(
                            message=texto,
                            user_id=app.storage.user["user_id"],
                            thread_id=app.storage.user["current_thread"],
                        )

                        full_response = ""
                        tool_state = {"name": None, "count": 1}

                        agent = await fastapi_state.state.agent_manager.get_agent()
                        async for chunk_str in stream_agent_response(
                            agent, usuario_obj
                        ):
                            if chunk_str.startswith("data: "):
                                data_str = chunk_str[6:].strip()
                                if not data_str:
                                    continue

                                try:
                                    data = json.loads(data_str)

                                    if data["type"] == "tool":
                                        # Nos aseguramos de mostrar el contenedor de tools
                                        tool_ui_container.classes(remove="hidden")

                                        tool_name = data["tools"][0]

                                        if tool_state["name"] == tool_name:
                                            tool_state["count"] += 1
                                        else:
                                            tool_state["name"] = tool_name
                                            tool_state["count"] = 1

                                        count_text = (
                                            f" (x{tool_state['count']})"
                                            if tool_state["count"] > 1
                                            else ""
                                        )
                                        tool_label.set_text(
                                            f"Consultando: {tool_name}{count_text}"
                                        )

                                        scroll_area.scroll_to(percent=1.0)

                                    elif data["type"] == "text":
                                        # Ocultamos el contenedor de tools (no lo borramos, por si usa otro tool después)
                                        tool_ui_container.classes(add="hidden")
                                        response_label.classes(remove="hidden")

                                        full_response += data["content"]
                                        response_label.set_content(full_response)
                                        scroll_area.scroll_to(percent=1.0)

                                except json.JSONDecodeError:
                                    pass

                    except Exception as e:
                        tool_ui_container.classes(add="hidden")
                        ui.notify(f"Error: {str(e)}", type="negative")
                        logger.exception(f"Error : {str(e)}")

                    finally:
                        local_state["is_processing"] = False
                        msg_input.enable()
                        send_btn.enable()
                        msg_input.run_method("focus")
                        scroll_area.scroll_to(percent=1.0)

                # Vincular eventos
                msg_input.on("keydown.enter", send)
                send_btn.on_click(send)

    @ui.page("/settings")
    async def settings_page():
        from app.settings_db import (
            get_all_settings,
            set_setting,
            get_connections,
            add_connection,
            remove_connection,
            get_llm_configs,
            set_active_llm_config,
        )
        from app.config import sqlcl_init_config

        ui.query("body").style("background-color: #f8fafc; color: #1e293b;")

        with ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-6"):
            ui.label("Agent Settings").classes("text-2xl font-bold text-slate-800")

            current = await get_all_settings()

            with ui.card().classes("w-full p-4 shadow-md rounded-xl"):
                ui.label("System Prompt").classes("text-sm font-semibold text-slate-500")
                system_prompt = (
                    ui.textarea(value=current.get("SYSTEM_PROMPT", ""))
                    .classes("w-full mt-1")
                    .props("outlined autogrow")
                )

            with ui.card().classes("w-full p-4 shadow-md rounded-xl"):
                ui.label("Filesystem Prompt").classes("text-sm font-semibold text-slate-500")
                filesystem_prompt = (
                    ui.textarea(value=current.get("FILESYSTEM_PROMPT", ""))
                    .classes("w-full mt-1")
                    .props("outlined autogrow")
                )

            with ui.card().classes("w-full p-4 shadow-md rounded-xl"):
                ui.label("Language").classes("text-sm font-semibold text-slate-500")
                ui.markdown("Replaces `{LANGUAGE}` in the system prompt").classes("text-xs text-slate-400 mt-0.5")
                language = (
                    ui.select(
                        options=["English", "Mandarin Chinese", "Hindi", "Spanish", "French", "Arabic", "Bengali", "Portuguese", "Russian", "Urdu"],
                        value=current.get("LANGUAGE", "Spanish"),
                        label="Response language",
                    )
                    .classes("w-full mt-1")
                    .props("outlined")
                )

            with ui.card().classes("w-full p-4 shadow-md rounded-xl"):
                ui.label("LLM Configuration").classes("text-sm font-semibold text-slate-500")

                current_config_id = None

                def _on_config_select(val):
                    nonlocal current_config_id
                    if val:
                        current_config_id = int(val)

                config_selector = (
                    ui.select(options=[], label="Active provider", on_change=lambda e: _on_config_select(e.value))
                    .classes("w-full mt-2")
                    .props("outlined")
                )

                async def _load_configs():
                    nonlocal current_config_id
                    configs = await get_llm_configs()
                    opts = {}
                    for c in configs:
                        label = f"{c['label']} ({c['provider']}: {c['model']})"
                        opts[str(c["id"])] = label
                    config_selector.set_options(opts)
                    if configs:
                        for c in configs:
                            if c.get("is_active"):
                                config_selector.value = str(c["id"])
                                return
                        first = configs[0]
                        config_selector.value = str(first["id"])

                await _load_configs()

            with ui.card().classes("w-full p-4 shadow-md rounded-xl"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("SQL Connections").classes("text-sm font-semibold text-slate-500")
                ui.markdown(
                    "_Connection details are retrieved via `connmgr show`, which does not return stored passwords. "
                    "Connections added via the interface or environment variables will show their full credentials._"
                ).classes("text-xs text-slate-400 mt-1")

                connections_container = ui.column().classes("w-full mt-2 gap-2")

                async def refresh_connections():
                    connections_container.clear()
                    conns = await get_connections()
                    with connections_container:
                        for c in conns:
                            with ui.row().classes("w-full items-center gap-2 p-2 bg-slate-50 rounded"):
                                ui.label(c["name"]).classes("font-medium text-sm w-32 truncate")
                                ui.label(c["connection_string"]).classes(
                                    "text-xs text-slate-500 truncate flex-1"
                                )
                                status_color = {
                                    "ok": "green",
                                    "failed": "red",
                                    "testing": "amber",
                                    "unknown": "grey",
                                }.get(c.get("status", "unknown"), "grey")
                                status_icon = {
                                    "ok": "check_circle",
                                    "failed": "error",
                                    "testing": "hourglass_empty",
                                    "unknown": "help",
                                }.get(c.get("status", "unknown"), "help")
                                ui.icon(status_icon, size="xs").classes(f"text-{status_color}-600")
                                ui.label(c.get("status", "unknown")).classes(
                                    f"text-xs text-{status_color}-600 w-16"
                                )
                                ui.button(
                                    icon="play_arrow",
                                    on_click=lambda _, cname=c["name"]: _test_connection(
                                        cname, refresh_connections
                                    ),
                                ).props("flat round dense size=sm").tooltip("Test connection")
                                ui.button(
                                    icon="delete",
                                    on_click=lambda _, cid=c["id"]: _delete_connection(
                                        cid, refresh_connections
                                    ),
                                ).props("flat round dense size=sm color=negative")

                await refresh_connections()

                with ui.row().classes("w-full mt-2 gap-2"):
                    new_conn_name = ui.input(placeholder="Name").props("outlined dense").classes("flex-1")
                    new_conn_string = ui.input(placeholder="user/pass@host:port/svc").props(
                        "outlined dense"
                    ).classes("flex-2")

                    async def add_conn():
                        add_btn.disable()
                        add_btn.props("loading")
                        try:
                            name = new_conn_name.value.strip()
                            cs = new_conn_string.value.strip()
                            if not name or not cs:
                                try:
                                    ui.notify("Name and connection string are required", type="warning")
                                except RuntimeError:
                                    pass
                                return
                            try:
                                await add_connection(name, cs)
                                await asyncio.to_thread(sqlcl_init_config, nombre_conexion=name, cadena_conexion=cs)
                                new_conn_name.value = ""
                                new_conn_string.value = ""
                                await refresh_connections()
                                try:
                                    ui.notify(f"Connection '{name}' added", type="positive")
                                except RuntimeError:
                                    pass
                            except Exception as e:
                                try:
                                    ui.notify(f"Error: {e}", type="negative")
                                except RuntimeError:
                                    pass
                        finally:
                            add_btn.enable()
                            add_btn.props(remove="loading")

                    add_btn = ui.button("Add", icon="add").props("flat color=primary")
                    add_btn.on_click(add_conn)

            async def _save_settings(sp, fp, lang, btn):
                btn.disable()
                btn.props("loading")
                settings_status.set_text("Saving...")
                try:
                    await set_setting("SYSTEM_PROMPT", sp.value)
                    await set_setting("FILESYSTEM_PROMPT", fp.value)
                    await set_setting("LANGUAGE", lang.value)

                    if current_config_id:
                        await set_active_llm_config(current_config_id)

                    new_settings = await get_all_settings()
                    await fastapi_state.state.agent_manager.recreate(new_settings)

                    db_connections = await get_connections()
                    for conn in db_connections:
                        try:
                            sqlcl_init_config(
                                nombre_conexion=conn["name"],
                                cadena_conexion=conn["connection_string"],
                            )
                        except Exception as e:
                            logger.warning(f"Error reinitializing connection {conn['name']}: {e}")

                    ui.notify("Agent recreated successfully", type="positive", position="top")
                    settings_status.set_text("Agent recreated successfully")
                except Exception as e:
                    ui.notify(f"Error: {e}", type="negative")
                    settings_status.set_text(f"Error: {e}")
                finally:
                    btn.enable()
                    btn.props(remove="loading")

            with ui.row().classes("w-full gap-4 mt-2"):
                save_btn = ui.button("Save & Recreate Agent", icon="refresh").props("color=primary")
                save_btn.on_click(lambda: _save_settings(
                    system_prompt, filesystem_prompt, language, save_btn,
                ))

                settings_status = ui.label("").classes("text-sm text-slate-500 pt-2")

        async def _delete_connection(conn_id: int, refresh_callback):
            try:
                await remove_connection(conn_id)
                await refresh_callback()
                try:
                    ui.notify("Connection removed", type="positive")
                except RuntimeError:
                    pass
            except Exception as e:
                try:
                    ui.notify(f"Error: {e}", type="negative")
                except RuntimeError:
                    pass

        async def _test_connection(name: str, refresh_callback):
            from app.config import sqlcl_test_connection
            from app.settings_db import update_connection_status

            try:
                ui.notify(f"Testing '{name}'...", type="info")
            except RuntimeError:
                pass

            try:
                await update_connection_status(name, "testing", "")
                await _safe_refresh(refresh_callback)
                ok, message = await asyncio.to_thread(sqlcl_test_connection, name)
                status = "ok" if ok else "failed"
                await update_connection_status(name, status, message)
                await _safe_refresh(refresh_callback)
            except Exception as e:
                await update_connection_status(name, "failed", str(e))
                await _safe_refresh(refresh_callback)


async def _safe_refresh(callback):
    try:
        await callback()
    except RuntimeError:
        pass
