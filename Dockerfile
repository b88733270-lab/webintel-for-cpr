# Image de production WebIntel (FastAPI + uvicorn).
FROM python:3.12-slim

# Variables d'environnement documentées :
#   DATABASE_URL        chaîne SQLAlchemy (défaut : sqlite:///./webintel.db ;
#                       en prod : postgresql+psycopg://webintel:<mot-de-passe>@db:5432/webintel)
#   SECRET_KEY          clé secrète (OBLIGATOIRE en prod, jamais la valeur dev)
#   REQUIRE_API_KEY     "true" en prod (auth X-API-Key obligatoire)
#   ENABLE_SCHEDULER    "true" (planificateur des moniteurs en process)
#   EXPORT_DIR          dossier persistant des exports (monter un volume en prod)
#   LOG_LEVEL           INFO par défaut
#   FETCH_TIMEOUT_S, MAX_RESPONSE_BYTES, MAX_REDIRECTS, OBEY_ROBOTS,
#   CRAWL_DELAY_S, MAX_CRAWL_PAGES, MAX_CRAWL_DEPTH, MAX_CONCURRENCY,
#   SCHEDULER_INTERVAL_S, DEFAULT_UA : voir webintel/config/settings.py
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:///./webintel.db \
    EXPORT_DIR=/app/webintel_data/exports \
    LOG_LEVEL=INFO

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "webintel.main:app", "--host", "0.0.0.0", "--port", "8000"]
