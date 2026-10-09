# Production image for the API (the worker uses the same image: `python -m app.worker`).
# Build context = repository root.
FROM python:3.12-slim AS build
WORKDIR /src
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    API_HOST=0.0.0.0 \
    API_PORT=8000
RUN useradd --system --uid 10001 --no-create-home stockflow
COPY --from=build /install /usr/local
# No secrets are baked in: every value arrives as an environment variable at run time.
USER stockflow
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('API_PORT','8000'), timeout=3).status == 200 else 1)"]
STOPSIGNAL SIGTERM
# `exec` makes uvicorn PID 1 so it receives SIGTERM and shuts down gracefully.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host $API_HOST --port $API_PORT"]
# Read-only filesystem: docker run --read-only --tmpfs /tmp ...  (the app writes nothing to disk)
