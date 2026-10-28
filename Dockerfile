FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 APP_ENV=production TRUSTED_PROXY_IPS=127.0.0.1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates unixodbc-dev && \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 && \
    rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY jcf_platform/ ./jcf_platform/
ARG INCLUDE_TEST_DEPS=0
RUN pip install --no-cache-dir . && \
    if [ "$INCLUDE_TEST_DEPS" = "1" ]; then pip install --no-cache-dir ".[test]"; fi && \
    useradd --system --no-create-home --uid 10001 appuser
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health/live',timeout=3).read()" || exit 1
CMD ["sh", "-c", "exec uvicorn jcf_platform.main:get_app --factory --host 0.0.0.0 --port 8080 --workers 2 --proxy-headers --forwarded-allow-ips \"$TRUSTED_PROXY_IPS\""]
