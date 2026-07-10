# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-07-08

### Fixed

- CI never published a version-numbered image tag (`0.2.1`/`0.2.1-cloning`),
  only `latest`/`latest-cloning` and short-SHA tags -- Supervisor's
  version-pinned pulls were silently broken.
- Docker `HEALTHCHECK` and `run.sh`'s readiness probe used `nc`, which
  doesn't exit until the connection closes or its idle timeout elapses --
  paying a fixed 2-5s floor per probe even when the response arrived
  instantly. Replaced with a proper Wyoming client (`wyoming_bluetts.probe`)
  that exits as soon as it gets a response; also drops the `netcat-openbsd`
  dependency from both Dockerfiles.
- README contradicted itself, calling the package both "pip-installable" and
  "must be installed with uv, not pip".
- Silenced a ruff deprecation warning by moving `select`/`ignore` under
  `[tool.ruff.lint]`.
- Documented the git tag and GitHub Release steps in `CONTRIBUTING.md`,
  missing since 0.2.1 shipped without either.

## [0.2.1] - 2026-07-06

### Added

- `--speak-decimal-points` (default on): speaks "3.5" as "3 point 5"
  (en/es/de/it). Also exposed as a Home Assistant app option.
- Logs synthesis time and real-time factor per request.
- `Dockerfile.cloning` now also built and published by CI
  (`latest-cloning`/`<version>-cloning`), no longer build-it-yourself only.
- CI smoke test: pulls each published tag and sends a real `Synthesize`
  request, catching broken images `pytest` alone can't.
- Home Assistant app now pulls the pre-built image instead of building
  locally -- faster installs/updates.

### Fixed

- GHCR package page showed "No description provided": `imagetools create`
  doesn't copy labels into the index it builds. Also dropped the
  auto-generated (and misleading, for a multi-component image)
  `org.opencontainers.image.licenses` label.
- `latest` and `latest-cloning` had the same description, wrongly claiming
  voice cloning on the non-cloning image.
- phonemizer's "words count mismatch" warning kept reappearing despite an
  earlier `setLevel` fix, because `blue_onnx` resets it per language on
  first use. Fixed with a `logging.Filter` instead.
- Voice cloning silently fell back to the default voice: missing `resampy`
  dependency (`blue_onnx.style` needs it for resampling, but neither
  `blue-onnx` nor `librosa` declares it).
- `Dockerfile.cloning` base image updated from Debian bookworm to trixie.

## [0.2.0] - 2026-07-05

### Added

- Initial release: Wyoming protocol server for BlueTTS.
- Multilingual synthesis (en, es, de, it, and opt-in he) with
  language-independent voices.
- Incremental audio streaming (sentence/paragraph-chunk granularity),
  advertised via `supports_synthesize_streaming`.
- Two built-in preset voices (`female1`, `male1`).
- Custom voices via precomputed style JSON, or (opt-in, see below) zero-shot
  cloning from a reference `.wav` clip.
- Automatic model bundle download (ONNX graphs + optional Hebrew G2P model)
  on first start.
- Home Assistant app packaging alongside the pip-installable package.
- `onnx`/`onnxslim` excluded from the Docker image: confirmed unused at
  runtime, ~40 MB saved.
- Zero-shot `.wav` voice cloning made opt-in at Docker build time
  (`--build-arg ENABLE_VOICE_CLONING=true`, default off): its
  librosa/numba/llvmlite/scipy/scikit-learn/sympy dependency chain is 400+ MB,
  roughly half the image, for a feature most installs never use. The
  published image and the Home Assistant app build (which can't pass custom
  build args through Supervisor) don't include it; precomputed style JSON
  custom voices still work everywhere, and requesting a `.wav`-only voice
  without cloning support logs a warning and falls back to the default voice
  instead of failing. Default image size: 902 MB -> 431 MB.
- The 3 zero-shot-cloning ONNX graphs (~118 MB) are now only downloaded when
  cloning is actually available in the build, instead of unconditionally --
  they were previously fetched even in a no-cloning image, where the code to
  use them isn't even present.
- Hebrew (`he`) removed from the default `--languages`/`languages` option: it
  needs an extra ~20 MB G2P model download that most installs don't need.
  Still fully supported; add it back explicitly if you want it.
- Removed `pip` and the now-orphaned transitive dependencies of already-excluded
  packages (`ml_dtypes` from `onnx`/`onnxslim`; `msgpack`/`audioread`/`decorator`/
  `lazy_loader`/`pooch`/`platformdirs`/`requests`/`charset_normalizer`/`urllib3`/
  `soxr` from `librosa`; `narwhals`/`threadpoolctl` from `scikit-learn`) --
  confirmed via a full `uv.lock` reverse-dependency check that nothing else
  needs them. Default image site-packages: 287 MB -> 240 MB.
- Removed the `espeak-ng` apt package (+ its own dependencies): confirmed
  unused at runtime -- `blue_onnx` wires phonemizer-fork directly to
  `espeakng_loader`'s bundled library/data files, and phonemizer-fork's espeak
  backend has no subprocess/CLI fallback that would need the system binary.
  Verified across all 5 languages. ~13 MB saved.
- `run.sh` rewritten in POSIX `sh` (was bash-specific: array-based argument
  building, `#!/usr/bin/env bash` shebang), so it no longer requires `bash`
  to be installed -- notably enabling the experimental Alpine build below to
  drop it. Also removed a dead `bashio` code path (this project never builds
  from an HA base image, so `bashio` is never actually present) that used
  bash-only `&>` redirection; under `dash` (Debian's `/bin/sh`) that redirect
  was silently misparsed and would have made the script take the (nonexistent)
  bashio branch and crash, a latent bug only surfaced by this rewrite and
  fixed as part of it.
- **The published/default image is now Alpine-based** (`Dockerfile`), not
  glibc: ~285 MB on amd64, ~364 MB on aarch64 (verified natively on real
  hardware on both) vs. the glibc build's ~377 MB / ~558 MB. See `AGENTS.md`
  for the workarounds it needed (a shim for `espeakng_loader`'s glibc-only
  bundled library, avoiding double-installing onnxruntime/numpy's Python
  files across build stages, an install-ordering fix for a stray `sympy`
  reinstall). It cannot support zero-shot voice cloning at all --
  `numba`/`llvmlite` don't build on musl (confirmed: a from-source build
  fails even with Alpine's own LLVM toolchain installed).
- The former default (glibc, `python:3.12-slim-bookworm`) is now
  `Dockerfile.cloning`, not built by CI -- build it yourself if you need
  voice cloning, which is on by default there (`ENABLE_VOICE_CLONING=true`).

### Known upstream issues

- `blue-onnx` is pinned to a specific git commit rather than a PyPI release:
  the published PyPI wheel (`0.2.4`) lacks the `blue_onnx.style` voice-cloning
  module entirely. Must be installed with `uv` (respects the pin via
  `[tool.uv.sources]`); plain `pip` would silently install the broken PyPI
  version.
- No build of `blue-onnx` (PyPI or the pinned git commit) ships `vocab.json`
  in its wheel, even though it's required for all languages. Worked around by
  vendoring a copy and installing it next to the `blue_onnx` package at
  startup (`models.ensure_blue_onnx_vocab()`).
