# 本地研究用映像：網頁介面與 worker 共用。基底映像以 digest 釘住，套件只取 2026-10-13 前已發布的版本。
FROM node:22.23.2-bookworm-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9 AS node

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CORPUS_DATA_DIR=/data \
    HF_HOME=/data/hf-cache \
    TORCH_HOME=/data/torch-cache

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime for YouTube; only the node binary is copied.
COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY requirements.txt requirements-model.txt ./
# The PyTorch CPU index lacks upload-time metadata, so torch/torchaudio come from it pinned exactly and
# without deps; every other package (torch's deps included) resolves from PyPI under the date cutoff.
RUN pip install pip==26.2.1 \
 && pip install --no-deps --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 torchaudio==2.8.0 \
 && pip install --uploaded-prior-to 2026-10-13T00:00:00Z -r requirements-model.txt

COPY twcorpus ./twcorpus
COPY scripts ./scripts
COPY db ./db
COPY tests ./tests

RUN groupadd --gid 1000 corpus \
 && useradd --uid 1000 --gid corpus --home-dir /home/corpus --create-home corpus \
 && mkdir -p /data \
 && chown corpus:corpus /data
USER corpus

EXPOSE 8501
VOLUME ["/data"]
CMD ["uvicorn", "twcorpus.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8501"]
