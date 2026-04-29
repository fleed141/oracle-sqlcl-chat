import re
import shutil
from pydantic_settings import BaseSettings
import subprocess
from loguru import logger
import os
import urllib.request
import zipfile
import stat
from pathlib import Path


class Settings(BaseSettings):

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = ""

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = ""

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = ""

    GOOGLE_API_KEY: str = ""
    GOOGLE_GEMINI_MODEL: str = ""

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = ""

    SQLCL_PATH: str = "/workspace/sqlcl_files/bin/sql"

    SYSTEM_PROMPT: str
    FILESYSTEM_PROMPT: str

    SQLCL_CONNECTIONS: str
    API_WORKERS: int = 1
    NICEGUI_STORAGE_SECRET: str
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGUAGE: str = "Spanish"

    class Config:
        env_file = ".env"


settings = Settings()


def parse_connection_string(cadena_bruta):
    # 1. Buscamos todo lo que esté dentro de corchetes [...]
    # re.findall devolverá una lista: ['nombreconexion,user/password@ip:puerto/servicename', 'nombreconexion2,user/password@ip:puerto/servicename']
    bloques = re.findall(r"\[(.*?)\]", cadena_bruta)

    lista_conexiones = []

    for bloque in bloques:
        # 2. Separamos cada bloque por la primera coma que encuentre.
        # Usamos maxsplit=1 por si de casualidad la contraseña tiene una coma.
        partes = bloque.split(",", 1)

        if len(partes) == 2:
            nombre = partes[0].strip()
            cadena_conn = partes[1].strip()

            lista_conexiones.append({"nombre": nombre, "cadena": cadena_conn})

    return lista_conexiones


def sqlcl_init_config(nombre_conexion, cadena_conexion):
    try:
        proc = subprocess.Popen(
            [f"{settings.SQLCL_PATH}", "/NOLOG"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        comandos = f"conn -sv -save {nombre_conexion} {cadena_conexion}\nexit\n"
        stdout, stderr = proc.communicate(input=comandos, timeout=15)

        if "saved" in stdout.lower() or "already exists" in stdout.lower():
            logger.info(f"Conexión '{nombre_conexion}' lista en SQLcl.")
        elif "Connection failed" in stdout:
            logger.warning(f"Conexión '{nombre_conexion}' guardada pero no validada (host unreachable).")
        else:
            logger.error("Hubo un problema al guardar:")
            logger.info(stdout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        logger.warning(f"Conexión '{nombre_conexion}' guardada con timeout (host unreachable).")
    except Exception as e:
        logger.exception(f"Error al ejecutar SQLcl: {str(e)}")
        raise


def _sqlcl_exec(comandos: str) -> str:
    proc = subprocess.Popen(
        [f"{settings.SQLCL_PATH}", "/NOLOG"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate(input=comandos, timeout=60)
    return stdout


_BANNER_JUNK = {"release", "production", "copyright", "all rights reserved", "sqlcl:"}


def sqlcl_list_connections() -> list[str]:
    stdout = _sqlcl_exec("set feedback off\nconnmgr list -flat\nexit\n")
    names = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(junk in lower for junk in _BANNER_JUNK):
            continue
        names.append(stripped)
    return names


def sqlcl_show_connection(nombre: str) -> dict | None:
    stdout = _sqlcl_exec(f"connmgr show {nombre}\nexit\n")
    result = {}
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("connect string:") or stripped.lower().startswith("connect string :"):
            result["connect_string"] = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("user:") or stripped.lower().startswith("user :"):
            result["user"] = stripped.split(":", 1)[1].strip()
    return result if result.get("connect_string") else None


def sqlcl_test_connection(nombre: str) -> tuple[bool, str]:
    stdout = _sqlcl_exec(f"connmgr test {nombre}\nexit\n")
    success = "Connection Test Successful" in stdout
    message = "\n".join(line.strip() for line in stdout.splitlines() if line.strip())
    return success, message


def check_for_sqlcl():
    # Usamos pathlib para un manejo de rutas más moderno y seguro
    ruta_base = Path.cwd()
    directorio_destino = ruta_base / "sqlcl_files"
    url_descarga = (
        "https://download.oracle.com/otn_software/java/sqldeveloper/sqlcl-latest.zip"
    )
    archivo_zip = ruta_base / "sqlcl_temp.zip"

    # 1. Comprobamos si el binario ya existe (el volumen está bien montado y con datos)
    if os.path.exists(settings.SQLCL_PATH):
        logger.info("Directorio de SQLcl detectado. Omitiendo descarga.")
        return

    # 2. Si no existe, iniciamos la descarga
    logger.warning(
        "No se detectó el binario de SQLcl. Descargando la versión más reciente..."
    )

    try:
        urllib.request.urlretrieve(url_descarga, archivo_zip)
        logger.info("Descarga completada. Extrayendo archivos...")

        # 3. Descomprimimos en una carpeta temporal para no ensuciar el directorio de trabajo
        temp_extract = ruta_base / "temp_extract"
        with zipfile.ZipFile(archivo_zip, "r") as zip_ref:
            zip_ref.extractall(temp_extract)

        # 4. El zip de Oracle siempre crea una carpeta raíz llamada 'sqlcl'.
        # Apuntamos a esa carpeta para sacar sus contenidos.
        carpeta_interna = temp_extract / "sqlcl"

        # 5. Movemos los contenidos DIRECTAMENTE a sqlcl_files.
        # dirs_exist_ok=True es clave aquí, ya que Docker crea la carpeta sqlcl_files vacía al montar el volumen.
        shutil.copytree(carpeta_interna, directorio_destino, dirs_exist_ok=True)

        # 6. CRÍTICO: Dar permisos de ejecución (chmod +x) al script de bash
        st = os.stat(settings.SQLCL_PATH)
        os.chmod(settings.SQLCL_PATH, st.st_mode | stat.S_IEXEC)

        logger.success(
            "SQLcl se descargó e instaló correctamente en el volumen sqlcl_files."
        )

    except Exception as e:
        logger.error(f"Error crítico al descargar/extraer SQLcl: {e}")
        raise e
    finally:
        # 7. Limpieza absoluta: borramos el zip y la carpeta temporal extraída
        if os.path.exists(archivo_zip):
            os.remove(archivo_zip)
        if "temp_extract" in locals() and os.path.exists(temp_extract):
            shutil.rmtree(temp_extract)
