# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-04

### Added

- Initial release: Wyoming protocol server for BlueTTS.
- Multilingual synthesis (en, es, de, it, he) with language-independent
  voices.
- Incremental audio streaming (sentence/paragraph-chunk granularity),
  advertised via `supports_synthesize_streaming`.
- Two built-in preset voices (`female1`, `male1`).
- Custom voices via precomputed style JSON, or zero-shot cloning from a
  reference `.wav` clip.
- Automatic model bundle download (ONNX graphs + optional Hebrew G2P model)
  on first start.
- Home Assistant app packaging alongside the pip-installable package.

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
