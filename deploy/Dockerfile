FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY detect_core ./detect_core
RUN pip install --no-cache-dir "./detect_core[llm]"
COPY . .

# Secrets come from env at runtime. DB_PATH / LLM_CACHE_DIR point at a mounted volume
# (e.g. /data); folders are created at startup, never here.
EXPOSE 8000
CMD ["sh", "deploy/start.sh"]
