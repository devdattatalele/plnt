# plnt playground API — OpenAI-compatible gateway.
# Build:  docker build -f docker/playground-api.Dockerfile -t plnt/playground-api:dev .
# Run:    docker run -p 8080:8080 plnt/playground-api:dev
#
# The image installs only the runtime dependencies the API actually imports.
# It does NOT pull the full plnt agent runtime (torch, faiss, etc.), keeping
# the image small (~150MB) and the cold-start fast.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN pip install --upgrade pip

# Install runtime deps first so the layer caches across code changes.
RUN pip install --prefix=/install \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "pydantic>=2.6" \
    "httpx>=0.27" \
    "sse-starlette>=2.0" \
    "anyio>=4.0"

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/install/bin:$PATH \
    PYTHONPATH=/install/lib/python3.12/site-packages:/app

COPY --from=builder /install /install

WORKDIR /app
COPY plnt/playground /app/plnt/playground
COPY plnt/__init__.py /app/plnt/__init__.py

# Non-root user for a smaller blast radius on shared clusters.
RUN groupadd --system plnt && useradd --system --gid plnt --uid 1001 plnt
USER 1001

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status==200 else 1)"

CMD ["uvicorn", "plnt.playground.api:app", "--host", "0.0.0.0", "--port", "8080"]
