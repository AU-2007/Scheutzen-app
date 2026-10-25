FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/data/verein.db TZ=Europe/Berlin
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd -r -u 1000 app && mkdir /data && chown app /data
COPY app/ ./app/
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/login')" || exit 1
# --preload: Datenbank wird genau einmal initialisiert, bevor die Worker starten
CMD ["gunicorn", "-w", "2", "--preload", "-b", "0.0.0.0:8000", "--chdir", "app", "app:app"]
