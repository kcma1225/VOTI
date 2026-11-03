# syntax=docker/dockerfile:1.7
# VOTI legacy-ml: Brouhaha (VAD/SNR/C50) + Vox-Profile (speaker/speech traits) inference service.
#
#   docker build --build-arg TORCH_VARIANT=cpu   -t voti-legacy-ml:cpu   services/legacy-ml
#   docker build --build-arg TORCH_VARIANT=cu128 -t voti-legacy-ml:cu128 services/legacy-ml
#
# Why a separate image: Brouhaha needs pyannote.audio <3.3.1 (3.x API: Inference.slide, map_with_specifications)
# and Vox-Profile pins transformers 4.46 (its custom Whisper/WavLM layers use 4.46 attention signatures);
# the main backend runs pyannote.audio 4 / recent transformers. Both upstreams *declare* Python 3.8 but run fine
# on 3.11 with torch 2.7 — which also gives real CUDA 12.8 wheels (Ampere/RTX A6000 sm_86 and newer).
# Licences: Brouhaha MIT; Vox-Profile code RAIL-S 1.1, model weights for non-commercial research use only.

ARG PYTHON_IMAGE=python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d

# ── upstream sources at pinned commits ───────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS upstream
ARG BROUHAHA_COMMIT=9132cbe62ac78f90abdbc21bcf6ec6cfe9bb4891
ARG VOXPROFILE_COMMIT=85100e60844a3f324a139e24fb9225aa6d8e45d1
ADD https://codeload.github.com/marianne-m/brouhaha-vad/tar.gz/${BROUHAHA_COMMIT} /tmp/brouhaha.tgz
ADD https://codeload.github.com/tiantiaf0627/vox-profile-release/tar.gz/${VOXPROFILE_COMMIT} /tmp/voxprofile.tgz
RUN set -eux; mkdir -p /opt/brouhaha /opt/vox-profile; \
    tar xzf /tmp/brouhaha.tgz -C /opt/brouhaha --strip-components=1; \
    tar xzf /tmp/voxprofile.tgz -C /opt/vox-profile --strip-components=1; \
    rm -rf /opt/brouhaha/analyses /opt/brouhaha/doc /opt/vox-profile/img; \
    # Vox-Profile hard-codes .cuda(); every wrapper owns a `weights` parameter, use its device instead
    sed -i -e 's/features\.input_features\.cuda()/features.input_features.to(self.weights.device)/' \
           -e 's/length = length\.cuda()/length = length.to(self.weights.device)/' \
           /opt/vox-profile/src/model/*/*.py; \
    ! grep -rn '\.cuda()' /opt/vox-profile/src/model; \
    test -s /opt/brouhaha/models/best/checkpoints/best.ckpt

# ── runtime ─────────────────────────────────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE}
ARG TORCH_VARIANT=cpu
ARG BROUHAHA_COMMIT=9132cbe62ac78f90abdbc21bcf6ec6cfe9bb4891
ARG VOXPROFILE_COMMIT=85100e60844a3f324a139e24fb9225aa6d8e45d1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv:/opt/brouhaha \
    MODEL_CACHE=/models HF_HOME=/models/hf TORCH_HOME=/models/torch \
    HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_ENABLE_HF_TRANSFER=0 TOKENIZERS_PARALLELISM=false \
    # pyannote.audio 3.3.0 loads Lightning checkpoints with torch.load defaults; torch>=2.6 defaults to weights_only
    TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 \
    BROUHAHA_COMMIT=${BROUHAHA_COMMIT} VOXPROFILE_COMMIT=${VOXPROFILE_COMMIT} \
    VOXPROFILE_ROOT=/opt/vox-profile BROUHAHA_CKPT=/opt/brouhaha/models/best/checkpoints/best.ckpt \
    AUDIO_CACHE_DIR=/tmp/legacy-ml-audio \
    TORCH_VARIANT=${TORCH_VARIANT}

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
COPY requirements/lock-${TORCH_VARIANT}.txt /tmp/lock.txt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps --index-strategy unsafe-best-match \
        --index-url https://download.pytorch.org/whl/${TORCH_VARIANT} --extra-index-url https://pypi.org/simple \
        -r /tmp/lock.txt \
    && python -c "import torch, torchaudio, pyannote.audio, transformers, speechbrain, loralib; print(torch.__version__, pyannote.audio.__version__, transformers.__version__)"

COPY --from=upstream /opt/brouhaha /opt/brouhaha
COPY --from=upstream /opt/vox-profile /opt/vox-profile
COPY app /srv/app
COPY tests /srv/tests

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /models/hf /models/torch /tmp/legacy-ml-audio \
    && chown -R 1000:1000 /models /tmp/legacy-ml-audio \
    && python -c "import app.server, brouhaha.models"
USER 1000:1000
WORKDIR /srv
EXPOSE 8600
VOLUME ["/models"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8600/healthz', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8600", "--workers", "1", "--timeout-keep-alive", "75"]
