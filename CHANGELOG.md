# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-04

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
