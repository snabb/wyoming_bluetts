# Wyoming BlueTTS Docker image (Alpine-based) -- this is the published/
# default image (built by CI, used by the Home Assistant app). Does NOT
# support zero-shot .wav voice cloning (numba/llvmlite, needed by that
# feature, don't build on musl -- see AGENTS.md). If you need cloning, build
# Dockerfile.cloning instead (glibc-based, `--build-arg
# ENABLE_VOICE_CLONING=true` by default) -- see its own header and README.
# Supports: amd64, aarch64 -- verified natively on both (see AGENTS.md).

# ============================================
# BUILDER STAGE
# ============================================
FROM alpine:3.24 AS builder

# py3-onnxruntime/py3-numpy: Alpine's community repo ships native musl builds
# of these (unlike PyPI, which has zero musllinux wheels for onnxruntime) --
# this is what makes an Alpine build possible at all now. Installed here only
# for their Python files (copied into the runtime stage below); the runtime
# stage installs the underlying C libraries itself (plain onnxruntime +
# openblas packages) rather than re-installing these py3- wrapper packages,
# to avoid paying for their site-packages tree twice.
# uv: Alpine's community repo ships it natively too (matching the main
# Dockerfile's tool choice, and much faster than pip for this many packages).
RUN apk add --no-cache \
    python3 \
    py3-onnxruntime \
    py3-numpy \
    uv \
    git

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    UV_BREAK_SYSTEM_PACKAGES=1

# Shim replacing the real (glibc-only) espeakng_loader PyPI package -- see
# alpine/espeakng_loader/__init__.py for why.
COPY alpine/espeakng_loader /usr/lib/python3.14/site-packages/espeakng_loader

# --no-deps + explicit installs below, rather than `uv pip install -r
# pyproject.toml`: onnxruntime and numpy must come from apk (no PyPI wheel
# for musl), and uv (like pip) has no way to say "treat this requirement as
# already satisfied" other than pre-installing it first, which apk already
# did. blue-onnx is git-pinned (see AGENTS.md in the main Dockerfile/repo).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps \
    "git+https://github.com/maxmelichov/BlueTTS.git@f071b9100e15c24575f6e2919312f67057c7b589"
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install \
    soundfile \
    huggingface-hub>=0.26.0 \
    phonemizer-fork>=3.3.2 \
    renikud-onnx \
    "wyoming>=1.7.0"

COPY pyproject.toml .
COPY wyoming_bluetts/ wyoming_bluetts/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-deps .

# Cleanup must be the LAST step in this stage, not right after apk add:
# renikud-onnx's plain `onnxruntime>=1.24.2` requirement makes uv (same as
# pip) re-resolve sympy from PyPI anyway (apk's py3-onnxruntime ships an
# old-style .egg-info with no proper Requires-Dist metadata, so neither tool
# can tell the already-installed copy satisfies it, and both re-derive its
# full dependency set, including onnxruntime's own optional "symbolic"
# extra) -- an earlier cleanup pass gets silently undone by that later
# install step. sympy/mpmath are unused by the plain ort.InferenceSession(...)
# path (same reasoning as the main Dockerfile's sympy/mpmath exclusion);
# numpy's own test suite is never needed at runtime either. Confirmed dead
# weight: ~75 MB combined. (No `pip` package to strip here, unlike the main
# Dockerfile -- uv doesn't install itself into site-packages.)
RUN rm -rf /usr/lib/python3.14/site-packages/sympy \
           /usr/lib/python3.14/site-packages/sympy-*.dist-info \
           /usr/lib/python3.14/site-packages/isympy.py \
           /usr/lib/python3.14/site-packages/mpmath \
           /usr/lib/python3.14/site-packages/mpmath-*.dist-info \
    && find /usr/lib/python3.14/site-packages/numpy -type d -name tests -prune -exec rm -rf {} +

# ============================================
# RUNTIME STAGE
# ============================================
FROM alpine:3.24

# onnxruntime/openblas: the C/C++ libraries py3-onnxruntime/py3-numpy dynamically
# load -- installed directly (not via the py3- wrapper packages, whose Python
# files already come from the builder stage's COPY below) to avoid double-
# paying for the same site-packages tree.
RUN apk add --no-cache \
    python3 \
    onnxruntime \
    openblas \
    espeak-ng \
    libsndfile \
    jq \
    curl \
    # run.sh's shebang is #!/bin/sh (POSIX, no bash-specific syntax), so
    # Alpine's built-in busybox ash (/bin/sh) runs it fine -- no bash needed.
    # protoc/libprotoc (the protobuf *compiler*, not the runtime libprotobuf/
    # libprotobuf-lite libraries onnxruntime actually needs) get pulled in by
    # this package combination for reasons not worth chasing through apk's
    # resolver -- confirmed unused at runtime and removed the same way as
    # every other dead-weight package in this project's Dockerfiles.
    && rm -rf /usr/lib/libprotoc.so* /usr/bin/protoc*

COPY --from=builder /usr/lib/python3.14/site-packages /usr/lib/python3.14/site-packages
COPY --from=builder /usr/bin/wyoming-bluetts /usr/bin/

WORKDIR /app

COPY run.sh /run.sh
RUN chmod +x /run.sh

RUN mkdir -p /data/models /share/tts-voices

EXPOSE 10200

HEALTHCHECK --interval=30s --timeout=10s --start-period=600s --retries=3 \
    CMD python3 -m wyoming_bluetts.probe 10200

CMD ["/run.sh"]
