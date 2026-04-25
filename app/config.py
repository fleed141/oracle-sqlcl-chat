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
        # Abrimos SQLcl sin logo para no ensuciar la salida
        proc = subprocess.Popen(
            [f"{settings.SQLCL_PATH}", "/NOLOG"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Enviamos el comando de guardado y luego exit

        comandos = f"conn -sv -save {nombre_conexion} {cadena_conexion}"

        stdout, stderr = proc.communicate(input=comandos)

        if "Connection saved" in stdout or "Connected" in stdout:
            logger.success(f"Conexión guardada con éxito.")
        else:
            logger.error("Hubo un problema al guardar:")
            logger.info(stdout)
    except Exception as e:
        logger.exception(f"Error al ejecutar SQLcl: {str(e)}")
        raise


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
