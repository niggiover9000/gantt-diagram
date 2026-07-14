FROM python:3.12-slim

# Nicht-Root-Benutzer für den Betrieb
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# Anwendungscode kopieren (nur was zur Laufzeit gebraucht wird)
COPY server.py gantt-editor.html ./

# Verzeichnis für die persistente Datenbank
RUN mkdir -p /data && chown -R appuser:appuser /app /data

USER appuser

# Datenbank liegt im Volume /data, Server lauscht auf 8000
ENV GANTT_DB=/data/gantt.db \
    GANTT_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

VOLUME ["/data"]

CMD ["python", "server.py"]
