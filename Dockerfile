FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . \
    && rm -rf /app/src /root/.cache \
    && useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin mybank \
    && mkdir -p /data /backups \
    && chown mybank:mybank /data /backups
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint

# Pas de USER ici : l'entrypoint abandonne root après avoir préparé les volumes (cas de l'add-on Home Assistant,
# dont /data appartient à root). Avec `user:` dans compose, il ne fait rien de plus que lancer la commande.
EXPOSE 8000
VOLUME ["/data", "/backups"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["docker-entrypoint"]
CMD ["uvicorn", "my_bank_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
