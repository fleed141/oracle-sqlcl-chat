FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV API_WORKERS=1
# Instalación de Java para SQLcl
RUN apt-get update && apt-get install -y \
    default-jre-headless \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos la estructura base
COPY sqlcl_files/ ./sqlcl_files/
COPY skills/ ./skills/
COPY app/ ./app/
# Definimos los volúmenes para persistencia
# Esto ayuda a que Docker sepa que estas rutas deben ser tratadas de forma special
VOLUME ["/workspace/memory", "/workspace/sqlcl_files", "/workspace/skills"]

CMD ["sh", "-c", "fastapi run app/main.py --workers $API_WORKERS"]