# Wyoming BlueTTS Docker image
# BlueTTS runs on ONNX Runtime (CPU only, no PyTorch dependency), so this image
# is much lighter than a PyTorch-based TTS wrapper.
# Supports: amd64, aarch64

# ============================================
# BUILDER STAGE - Install dependencies with uv
# ============================================
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# Zero-shot .wav voice cloning (blue_onnx.style) needs a librosa/numba/llvmlite/
# scipy/scikit-learn/sympy dependency chain that's 400+ MB -- roughly half the
# image -- for a feature most installs never use (precomputed style JSON
# custom voices work fine without it). Off by default to keep the common case
# small; build with `--build-arg ENABLE_VOICE_CLONING=true` to keep it.
ARG ENABLE_VOICE_CLONING=false

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# git: blue-onnx is pinned to a git commit via [tool.uv.sources] in
# pyproject.toml (see AGENTS.md), so uv needs git to fetch it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libsndfile1 \
    libsndfile1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r pyproject.toml

COPY wyoming_bluetts/ wyoming_bluetts/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps .

# blue-onnx hard-requires onnx/onnxslim in its own pyproject.toml (for its
# exports/ conversion tooling, which this project never calls), so there's no
# resolver flag to skip installing them -- remove them post-install instead.
# Verified unused: blue_onnx's only two source files (__init__.py, style.py)
# only ever do `import onnxruntime as ort`; onnxruntime itself doesn't depend
# on onnx (PyPI metadata), and the only onnx references inside onnxruntime's
# own package live in optional submodules (quantization/, tools/, backend/,
# transformers/) that the plain ort.InferenceSession(...) path never imports.
# Re-verify this whenever the blue-onnx pin in pyproject.toml is bumped, in
# case a future version starts using them.
RUN rm -rf /usr/local/lib/python3.12/site-packages/onnx \
           /usr/local/lib/python3.12/site-packages/onnx-*.dist-info \
           /usr/local/lib/python3.12/site-packages/onnxslim \
           /usr/local/lib/python3.12/site-packages/onnxslim-*.dist-info

# See the ENABLE_VOICE_CLONING ARG comment above. handler.py/__main__.py
# soft-import blue_onnx.style, so removing it here disables cloning
# gracefully (logs a warning, falls back to precomputed style JSON) instead
# of crashing. Re-verify this package list whenever the blue-onnx pin bumps.
RUN if [ "$ENABLE_VOICE_CLONING" != "true" ]; then \
        rm -rf /usr/local/lib/python3.12/site-packages/librosa \
               /usr/local/lib/python3.12/site-packages/librosa-*.dist-info \
               /usr/local/lib/python3.12/site-packages/numba \
               /usr/local/lib/python3.12/site-packages/numba-*.dist-info \
               /usr/local/lib/python3.12/site-packages/llvmlite \
               /usr/local/lib/python3.12/site-packages/llvmlite-*.dist-info \
               /usr/local/lib/python3.12/site-packages/scipy \
               /usr/local/lib/python3.12/site-packages/scipy.libs \
               /usr/local/lib/python3.12/site-packages/scipy-*.dist-info \
               /usr/local/lib/python3.12/site-packages/sklearn \
               /usr/local/lib/python3.12/site-packages/scikit_learn.libs \
               /usr/local/lib/python3.12/site-packages/scikit_learn-*.dist-info \
               /usr/local/lib/python3.12/site-packages/sympy \
               /usr/local/lib/python3.12/site-packages/sympy-*.dist-info \
               /usr/local/lib/python3.12/site-packages/isympy.py \
               /usr/local/lib/python3.12/site-packages/mpmath \
               /usr/local/lib/python3.12/site-packages/mpmath-*.dist-info \
        ; fi

# ============================================
# RUNTIME STAGE - Minimal final image
# ============================================
FROM python:3.12-slim-bookworm

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# espeak-ng: phonemizer-fork's CLI-subprocess fallback path needs the binary,
# even though espeakng-loader bundles its own library for the primary path.
# netcat/jq/curl: healthcheck + run.sh config parsing + HA discovery POST.
RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng \
    libsndfile1 \
    netcat-openbsd \
    jq \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/cache/apt/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/wyoming-bluetts /usr/local/bin/

WORKDIR /app

COPY run.sh /run.sh
RUN chmod +x /run.sh

RUN mkdir -p /data/models /share/tts-voices

EXPOSE 10200

# start-period is generous (10 min) since the model bundle downloads on first
# run and has 8 ONNX graphs; tighten once real download times are measured.
HEALTHCHECK --interval=30s --timeout=10s --start-period=600s --retries=3 \
    CMD echo '{"type":"describe"}' | nc -w 5 localhost 10200 | grep -q "bluetts" || exit 1

CMD ["/run.sh"]
