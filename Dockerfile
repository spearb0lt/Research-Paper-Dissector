# The full application, every capability switched on.
#
# Two stages: Node builds the frontend, then the Python image serves both it
# and the API. The frontend build output is copied in rather than rebuilt, so
# the runtime image carries no Node toolchain.
#
# This image is the reference target for an Oracle Always Free VM, which has
# 24 GB of RAM and runs the deep parser, OCR, CLIP and reranking comfortably.

FROM node:22-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json next.config.mjs postcss.config.mjs tailwind.config.ts next-env.d.ts ./
COPY app ./app
COPY components ./components
COPY lib ./lib
RUN npm run build


FROM python:3.12-slim AS runtime

# libGL and libglib are needed by OpenCV, which RapidOCR and Docling both pull
# in. Without them the import succeeds and the first call fails, which is a
# confusing way to discover a missing system library.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

# The CPU wheel explicitly. The default PyPI torch carries several hundred
# megabytes of CUDA libraries that are useless on a CPU host.
COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements-server.txt

COPY server ./server
COPY api ./api
COPY scripts ./scripts

# The bundled ONNX encoder, so a container with no network still has local
# embeddings on first run.
RUN python scripts/fetch_model.py --quiet || true

COPY --from=frontend /build/.next ./.next
COPY --from=frontend /build/public ./public
COPY package.json ./

RUN mkdir -p /data && chmod 777 /data
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "65"]
