from loguru import logger
from nicegui import ui, app
from app.models import UserMessage
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
                            preferences={},
                        )

                        full_response = ""
                        tool_state = {"name": None, "count": 1}

                        async for chunk_str in stream_agent_response(
                            fastapi_state.state.agent, usuario_obj
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
