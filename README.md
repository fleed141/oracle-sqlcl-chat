# Oracle Chat - MCP Agent

Este proyecto es un agente conversacional impulsado por IA, diseñado para interactuar con bases de datos Oracle. Utilizando el estándar Model Context Protocol (MCP) a través de Oracle SQLcl, el agente es capaz de ejecutar consultas, analizar esquemas y mantener el historial de contexto de cada usuario.

**Aviso Importante:** Esta implementación tiene un propósito estrictamente experimental y educativo, orientado a explorar las capacidades del servicio MCP de SQLcl. No está diseñado para operar en entornos de producción. Se recomienda encarecidamente conectar este agente de forma exclusiva a bases de datos de prueba o desarrollo, utilizando un usuario con permisos exclusivos de lectura.

A nivel de arquitectura, el sistema ofrece flexibilidad mediante dos vías de acceso, además de soportar múltiples proveedores de modelos de lenguaje (LLMs):

**Interfaz Gráfica (NiceGUI):** Una implementación básica incluida con el único propósito de servir como demostración y permitir pruebas rápidas del agente.

**API REST (FastAPI):** Endpoints expuestos diseñados para integraciones con sistemas existentes (Por ejemplo una pagina en Oracle APEX)

El proyecto está diseñado con una arquitectura modular y orientada a agentes, combinando herramientas de orquestación de IA con protocolos de integración de bases de datos modernos:

- **Orquestación y Estado (LangGraph):** El núcleo lógico del agente opera sobre LangGraph. El manejo de estado y la persistencia de las conversaciones (hilos) se gestionan mediante `AsyncSqliteSaver`, mientras que la memoria a largo plazo del agente utiliza `AsyncSqliteStore`.
- **Capa de Base de Datos (MCP + SQLcl):** A diferencia de las conexiones tradicionales JDBC/OCI, el agente se comunica con Oracle utilizando el estándar Model Context Protocol (MCP). El sistema levanta un subproceso de Oracle SQLcl en modo `-mcp` (comunicación vía `stdio`) y utiliza `langchain_mcp_adapters` para cargar dinámicamente las herramientas de base de datos como funciones ejecutables por el LLM.
- **Gestión de Contexto (Middlewares):** Para manejar conversaciones extensas y resultados de consultas masivos sin desbordar la ventana de contexto del LLM, la arquitectura implementa una cadena de middlewares:
  - _SummarizationMiddleware:_ Resume automáticamente el historial de la conversación cuando se supera un umbral crítico de tokens (ej. 20,000 tokens).
  - _ContextEditingMiddleware:_ Limpia y elimina del historial las respuestas antiguas de uso de herramientas (Tool Calls) reteniendo solo las más recientes.
  - _FilesystemMiddleware:_ Proporciona un sistema de archivos virtual, aislado por ID de usuario, permitiendo al agente guardar notas y preferencias de manera persistente.
- **Frontend y API (FastAPI + NiceGUI):** FastAPI expone los endpoints REST para consumo externo (incluyendo Server-Sent Events para streaming de respuestas). Sobre la misma instancia de FastAPI se monta NiceGUI, proporcionando la interfaz web nativa basada en componentes sin requerir un servidor frontend separado.
- **Capa Multi-Modelo:** Se utiliza una capa de abstracción de modelos que permite intercambiar el proveedor de LLM (Google, Anthropic, OpenAI, DeepSeek) únicamente modificando variables de entorno, sin alterar la lógica del agente.

## Requisitos Previos

- Docker o Podman instalados en el servidor host.
- Clave de API de tu proveedor de IA preferido (Google Gemini, DeepSeek, OpenRouter, Anthropic u OpenAI).
- Credenciales de acceso a tu base de datos Oracle.

## Configuración del Entorno

Antes de desplegar, debes definir tus variables de entorno. Crea un archivo `.env` en la raíz de tu proyecto o configúralas directamente en tu orquestador:

```env
# Zona Horaria
TZ=America/Asuncion

# Prompts base del sistema
SYSTEM_PROMPT="You are Oracle, an expert assistant in SQL and databases. You help users query and manage their databases using SQL. Make sure to always respond in {LANGUAGE}, no other language is allowed. You always respond with information based on correct and optimized SQL queries, and you briefly explain your reasoning. When writing these queries, always use DBA views. If the user provides additional information or context, you take it into account to generate your responses. If you make a mistake, correct it in the next response. You must not execute SQL queries that can modify the database, only read-only queries (SELECT). You must not show SQL queries to the user."
FILESYSTEM_PROMPT="Save relevant information about the user to your memory so you can use it in future conversations..."

# Conexiones a Oracle (Sintaxis estricta)
SQLCL_CONNECTIONS=[conn_name,user/password@ip:port/servicename]

# Configuración de la API y UI
API_WORKERS=1
NICEGUI_STORAGE_SECRET="tu_clave_secreta_aqui"

# Langsmith para trazabilidad del agente
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=""

# Proveedor de IA (Ejemplo con Gemini)
GOOGLE_API_KEY="tu_api_key_aqui"
GOOGLE_GEMINI_MODEL="google_genai:gemini-3.1-flash-lite-preview"

#OPENROUTER_API_KEY="aaaaaaaaaaaaaaaaaaaaa"
#OPENROUTER_MODEL="auto"

#ANTHROPIC_API_KEY="aaaaaaaaaaaaaaaaaaaaa"
#ANTHROPIC_MODEL="claude-sonnet-4-6"

#DEEPSEEK_API_KEY="aaaaaaaaaaaaaaaaaaa"
#DEEPSEEK_MODEL="deepseek-chat"

#OPENAI_API_KEY="aaaaaaaa"
#OPENAI_MODEL="openai:gpt-5.4"
```

## Instrucciones de Despliegue

Puedes levantar el servicio utilizando Docker Compose o Podman Quadlets.

### Opción A: Despliegue con Docker Compose

Asegúrate de tener el archivo docker-compose.yml en tu directorio.

Levanta el contenedor en segundo plano:

```Bash
docker compose up -d
```

Verifica los logs para asegurar que el agente inicializó correctamente:

```Bash
docker compose logs -f
```

### Opción B: Despliegue con Podman (Quadlet Root-Level)

El proyecto está preparado para ejecutarse como un servicio del sistema mediante systemd usando Podman.

Crea o copia el archivo Quadlet .container en el directorio de systemd:

```Bash
sudo cp oracle-chat.container /etc/containers/systemd/
```

Recarga los demonios de systemd para que reconozca el nuevo servicio:

```Bash
sudo systemctl daemon-reload
```

Habilita e inicia el servicio:

```Bash
sudo systemctl enable --now oracle-chat
```

Revisa los logs en tiempo real:

```Bash
sudo journalctl -fu oracle-chat
```

### Opcion C: Ejecución con Docker CLI (docker run)

La traducción directa del archivo Compose a un comando interactivo requiere mapear todas las variables de entorno (-e) y los volúmenes (-v).

```Bash
docker run -d \
  --name oracle-chat \
  --restart on-failure \
  -p 8000:8000 \
  -e TZ=America/Asuncion \
  -e SYSTEM_PROMPT="You are Oracle, an expert assistant in SQL and databases..." \
  -e FILESYSTEM_PROMPT="Save relevant information about the user to your memory..." \
  -e SQLCL_CONNECTIONS="[conn_name,user/password@ip:port/servicename]" \
  -e API_WORKERS=1 \
  -e NICEGUI_STORAGE_SECRET="supermegadupersecretkey" \
  -e GOOGLE_API_KEY="aaaaaaaaaaaaaaaaaa" \
  -e GOOGLE_GEMINI_MODEL="google_genai:gemini-3.1-flash-lite-preview" \
  -v oracle_chat_memory:/workspace/memory \
  -v oracle_chat_sqlcl:/workspace/sqlcl_files \
  ghcr.io/fleed141/agente-mcp:latest
```

### Opcion D: Ejecución con Podman CLI (podman run)

Al instanciar el runtime de Podman a nivel root, el comando es estructuralmente idéntico al de Docker

```Bash
sudo podman run -d \
  --name oracle-chat \
  --restart on-failure \
  -p 8000:8000 \
  -e TZ=America/Asuncion \
  -e SYSTEM_PROMPT="You are Oracle, an expert assistant in SQL and databases..." \
  -e FILESYSTEM_PROMPT="Save relevant information about the user to your memory..." \
  -e SQLCL_CONNECTIONS="[conn_name,user/password@ip:port/servicename]" \
  -e API_WORKERS=1 \
  -e NICEGUI_STORAGE_SECRET="supermegadupersecretkey" \
  -e GOOGLE_API_KEY="aaaaaaaaaaaaaaaaaa" \
  -e GOOGLE_GEMINI_MODEL="google_genai:gemini-3.1-flash-lite-preview" \
  -v oracle_chat_memory:/workspace/memory \
  -v oracle_chat_sqlcl:/workspace/sqlcl_files \
  ghcr.io/fleed141/agente-mcp:latest
```

### Uso del Servicio

Una vez que el contenedor esté corriendo (expuesto en el puerto 8000), tienes tres formas de interactuar con el agente:

**1. Interfaz Gráfica (Web UI)**
Accede desde cualquier navegador a la interfaz interactiva proporcionada por NiceGUI:

URL: http://tu-ip-o-dominio:8000/gui

**2. API de Streaming (Recomendado para integraciones)**
Ideal para conectar con frontends externos como Oracle APEX. Devuelve la respuesta token por token vía Server-Sent Events (SSE).

Endpoint: `POST /api/v1/chat-stream/`

Payload:

```JSON
{
  "mensaje": "Muestra el top 5 de tablas más pesadas",
  "user_id": "usuario_1",
  "thread_id": "conversacion_123"
}
```

**3. API Síncrona**
Espera a que el agente termine todo su razonamiento y herramientas antes de devolver la respuesta completa.

Endpoint: `POST /api/v1/chat/`

Payload: Mismo formato JSON que la ruta de streaming.

**Persistencia de Datos (Volúmenes)**
El contenedor maneja dos volúmenes principales para persistencia:

- memory/: Almacena la base de datos SQLite de LangGraph, manteniendo el historial de chats y el contexto de usuario.

- sqlcl_files/: Almacena los binarios de Oracle SQLcl. Si no se montan binarios personalizados, la imagen descargará automáticamente la última versión en el primer arranque y la conservará aquí.

## Personalización de Habilidades (Skills)

El agente permite extender sus capacidades operativas mediante la inyección de herramientas o habilidades (skills) personalizadas, sin necesidad de modificar el código fuente o reconstruir la imagen del contenedor.

Para habilitar esta característica, debes montar un directorio local que contenga tus propias definiciones dentro del contenedor.

### 1. Estructura del Directorio Local

Crea una carpeta en tu servidor host (por ejemplo, `./mis_skills`). Dentro de este directorio, el sistema requiere una estructura específica:

* **Archivo `SKILLS.md` (Obligatorio):** Este archivo actúa como el índice de tus habilidades. Su contenido se inyecta directamente en el contexto del agente, explicándole qué nuevas herramientas tiene a su disposición, para qué sirven y cómo debe invocarlas.

**Ejemplo:**

```
- mis_skills (Directory)
    - skill_category_1 (Directory)
        - skill1.md (File)
        - skill2.md (File)
    - skill_category_2 (Directory)
        - skill3.md (File)
        - skill4.md (File)
    - skill_category_3 (Directory)
        - skill5.md (File)
        - skill6.md (File)
    - SKILLS.md (File)
```
### 2. Montaje del Volumen

Debes mapear tu carpeta local al directorio interno `/workspace/skills` del contenedor.

**En Docker Compose:**
Añade la siguiente línea en la sección de volúmenes de tu servicio:

```yaml
volumes:
  # Agrega esta línea apuntando a tu carpeta local
  - ./mis_skills:/workspace/skills:z
```

**Creditos:**

Las skills cargadas por defecto fueron extraidas del repositorio: https://github.com/krisrice/oracle-db-skills
