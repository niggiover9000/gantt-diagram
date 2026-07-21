FROM python:3.12-slim

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py gantt-editor.html ./
COPY templates/ ./templates/

RUN mkdir -p /data && chown -R appuser:appuser /app /data

USER appuser

ENV GANTT_DB=/data/gantt.db \
    GANTT_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

VOLUME ["/data"]

CMD ["python", "server.py"]
