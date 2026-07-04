# Agent Guidelines

- Use `uv` for all dependency management (`uv add`, `uv run`). Never use
  `requirements.txt`, and prefer `uv pip` over bare `pip` anywhere a command
  is needed.
- Python 3.12+ (required by `blue-onnx`). Use modern type annotations
  (`list`, `dict`, `X | None`).
- Before committing: run `prek run --all-files` and `uv run -m pytest`. All
  hooks and tests must pass.
- This repo's default branch is `master`, not `main` (unlike the
  `wyoming_pocket_tts` template it was copied from). `.github/workflows/on-merge.yml`
  and `on-pr.yml` must trigger on `master` -- if this drifts back to `main`
  the ghcr.io image build/push silently never runs again.
- This is a Wyoming protocol TTS server for Home Assistant. Changes must
  maintain compatibility with the Wyoming protocol and Home Assistant's app
  (Supervisor add-on) system.
- **Terminology**: Home Assistant renamed "add-ons" to "apps" in user-facing
  text (Settings → Apps → Install app → ⋮ → Repositories). Use "app" in
  README/DOCS.md prose. The underlying Supervisor manifest files
  (`config.yaml`, `repository.yaml`) and the developers.home-assistant.io
  `/docs/add-ons/...` doc URLs are unchanged -- don't rename those files or
  "fix" those URLs.
- **No `build.yaml`, on purpose**: Supervisor deprecated `build.yaml` (base
  image per architecture, build args, labels) in favor of the Dockerfile
  handling all of that itself. Since Supervisor 2026.04.0 it no longer passes
  `BUILD_FROM`/`BUILD_ARCH`/`BUILD_VERSION` build-args when `build.yaml` is
  absent, and a present-but-unparsable `build.yaml` (e.g. a `build_from` value
  without a `namespace/repo` shape, like a bare `python:3.12-slim-bookworm`)
  logs a Supervisor warning on every install. Our Dockerfile already hardcodes
  its `FROM` lines directly and never consumed `BUILD_FROM`, so there's
  nothing to move -- don't re-add `build.yaml`.
- The project wraps [BlueTTS](https://github.com/maxmelichov/BlueTTS)
  (`blue-onnx` package). Voice handling (preset + custom + cloning) is in
  `wyoming_bluetts/handler.py`; model auto-download is in
  `wyoming_bluetts/models.py`.
- **Streaming design**: BlueTTS's `tts(...)` call is blocking and returns one
  whole-clip buffer per call — it has no native frame-by-frame generator.
  `handler._iter_audio_pcm_chunks()` recovers incremental delivery by
  splitting text into sentence/paragraph chunks (`blue_onnx.chunk_text`) and
  calling the engine once per chunk, flushing each chunk's audio to the
  client before starting the next chunk's synthesis. Do not "simplify" this
  back into a single whole-text call — that reintroduces the latency the
  streaming design exists to avoid. The server advertises
  `supports_synthesize_streaming=True`, which makes it mandatory to always
  send a `SynthesizeStopped` event after `AudioStop` (including on the
  exception path) — omitting it hangs Home Assistant's streaming client
  forever.
- **Renikud CWD workaround**: `blue_onnx.TextProcessor.__init__` only
  auto-discovers the Hebrew G2P model via a hardcoded relative check
  (`os.path.exists("model.onnx")` in the process's current working
  directory), and `load_text_to_speech()` doesn't expose a way to pass a
  custom path. `__main__.py` works around this by placing `model.onnx`
  directly in `models_dir` and `chdir`-ing there before loading the engine.
  This is intentional, not a bug — don't "fix" it by removing the `chdir`.
- **`blue-onnx` is pinned to a git commit, not a PyPI release**: as of this
  writing, the published PyPI wheel (`0.2.4`) is missing `blue_onnx.style`
  (zero-shot wav cloning, needs `>=0.2.5`) entirely, so `pyproject.toml`
  points `blue-onnx` at `git+https://github.com/maxmelichov/BlueTTS.git`
  pinned to an exact commit via `[tool.uv.sources]`. Switch back to a plain
  PyPI version constraint once a release with `blue_onnx.style` is published,
  re-testing `models.ensure_blue_onnx_vocab()` below against it (the
  vocab.json bug may or may not be fixed in that release too).
- **`vocab.json` packaging workaround**: no build of `blue-onnx` (checked both
  the published PyPI 0.2.4 wheel and a from-source build of the exact git
  commit pinned here) actually includes `vocab.json` in its wheel, even
  though `blue_onnx.load_text_processor()` hardcodes the tokenizer vocab path
  as `<installed-package-dir>/../vocab.json` -- a genuine upstream packaging
  gap, needed unconditionally for every language, not just Hebrew.
  `wyoming_bluetts/vocab.json` vendors a copy (identical content, from
  BlueTTS's own repo) and `models.ensure_blue_onnx_vocab()` copies it into
  place next to the installed `blue_onnx` package at startup. Re-check this
  is still necessary whenever the `blue-onnx` pin is bumped.
