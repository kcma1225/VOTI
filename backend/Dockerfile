# syntax=docker/dockerfile:1.7
# VOTI backend image: API + all workers (fetch / audio / ml / export).
#   docker build -f backend/Dockerfile --build-arg TORCH_VARIANT=cpu   -t voti-backend:cpu   .
#   docker build -f backend/Dockerfile --build-arg TORCH_VARIANT=cu128 -t voti-backend:cu128 .
# Build context is the repo root; backend/Dockerfile.dockerignore keeps it to backend/requirements + backend/voti.
# Base images pinned by digest; Python packages only from releases before 2026-10-13.
FROM node:22.23.2-bookworm-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9 AS node

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

# cpu | cu128 — selects the PyTorch wheel index (and onnxruntime-gpu for cu128)
ARG TORCH_VARIANT=cpu
ARG TORCH_VERSION=2.8.0
ARG PIP_CUTOFF=2026-10-13T00:00:00Z
# onnxruntime-gpu >= 1.28 on PyPI is built for CUDA 13; 1.26.0 is the newest build verified to run on the CUDA 12.8
# libraries that ship with the torch cu128 wheels (loaded via onnxruntime.preload_dlls()).
ARG ONNXRUNTIME_GPU_VERSION=1.26.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    TORCH_VARIANT=${TORCH_VARIANT} \
    MODEL_CACHE=/models \
    HF_HOME=/models/hf \
    TORCH_HOME=/models/torch \
    WORK_DIR=/work \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PYANNOTE_METRICS_ENABLED=false \
    NUMBA_CACHE_DIR=/tmp/numba

# ffmpeg/ffprobe (bookworm: FFmpeg 5.1 → libavutil57, which torchcodec 0.7 loads); ca-certificates for model downloads
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime for YouTube; only the node binary is copied.
COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY backend/requirements /app/requirements

# The PyTorch index lacks upload-time metadata, so torch/torchaudio come from it pinned exactly and without deps;
# every other package (torch's own deps — incl. the nvidia-* CUDA wheels for cu128 — included) resolves from
# PyPI under the date cutoff. fetch.txt is optional so the image also builds before the fetch worker exists.
RUN set -eux; \
    pip install pip==26.2.1; \
    pip install --no-deps --index-url "https://download.pytorch.org/whl/${TORCH_VARIANT}" \
        "torch==${TORCH_VERSION}" "torchaudio==${TORCH_VERSION}"; \
    reqs="-r requirements/base.txt -r requirements/ml.txt"; \
    if [ -f requirements/fetch.txt ]; then reqs="$reqs -r requirements/fetch.txt"; fi; \
    pip install --uploaded-prior-to "${PIP_CUTOFF}" $reqs "torch==${TORCH_VERSION}" "torchaudio==${TORCH_VERSION}"; \
    if [ "${TORCH_VARIANT}" != "cpu" ]; then \
        pip uninstall -y onnxruntime; \
        pip install --uploaded-prior-to "${PIP_CUTOFF}" "onnxruntime-gpu==${ONNXRUNTIME_GPU_VERSION}"; \
    fi; \
    python -c "import torch, torchaudio, speechbrain, pyannote.audio, silero_vad, onnxruntime, librosa, sklearn; \
print('torch', torch.__version__, 'onnxruntime', onnxruntime.__version__, onnxruntime.get_available_providers())"

COPY backend/voti /app/voti

RUN groupadd --gid 1000 voti \
 && useradd --uid 1000 --gid voti --home-dir /home/voti --create-home voti \
 && mkdir -p /models /work \
 && chown voti:voti /models /work
USER voti

CMD ["python", "-m", "voti.workers.run", "--queues", "fetch,audio,ml,export"]
