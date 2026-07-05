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
- **Voice cloning is optional and off by default in the Docker image**:
  `blue_onnx.style` (zero-shot `.wav` cloning) pulls in a
  librosa/numba/llvmlite/scipy/scikit-learn/sympy chain that's 400+ MB --
  roughly half the image. The `Dockerfile`'s `ENABLE_VOICE_CLONING` build arg
  (default `false`) strips those packages post-install (same "install then
  `rm -rf`" pattern as the `onnx`/`onnxslim` cleanup above). Because of this,
  `handler.py` and `__main__.py` both **soft-import** `blue_onnx.style`
  (`try`/`except ImportError`, mirroring the earlier pattern used while
  `blue_onnx.style` didn't exist on PyPI at all) -- `style_extractor` can be
  `None` at runtime in the default image, and `load_voice()`'s `.wav` branch
  must keep handling that gracefully (log + fall back to the default voice),
  not assume it's always present. Local `uv sync`/dev installs always have
  the full dependency set (blue-onnx declares `librosa` unconditionally), so
  `style_extractor` is only ever `None` in a default-built Docker image, not
  in dev/tests. When adding new code that touches `style_extractor`, keep it
  `None`-safe.
- **Model download mirrors the same cloning on/off split**: `models.py` keeps
  `CORE_BUNDLE_FILES` (always downloaded) separate from `CLONING_BUNDLE_FILES`
  (the 3 zero-shot-cloning ONNX graphs, ~118 MB) and only fetches the latter
  when `ensure_model_bundle(..., include_cloning=...)` is called with
  `include_cloning=True` -- `__main__.py` passes
  `VoiceStyleExtractor is not None` for this. Downloading the cloning graphs
  unconditionally would waste disk space in the default (no-cloning) image,
  since the code to use them isn't even importable there.
- **Hebrew ("he") is not in the default `--languages`**: it needs an extra
  ~20 MB G2P (renikud) model download that most installs don't need. It's
  still fully supported -- pass `--languages ...,he` (or the HA app's
  `languages` option) to enable it. Keep `DEFAULT_LANGUAGES` in `__main__.py`,
  `config.yaml`'s `options.languages`, `run.sh`'s fallback defaults, and the
  `docker-compose.yml` example in sync if this changes again.
- **`run.sh` is POSIX `sh`, not bash**: shebang is `#!/bin/sh`, and it must
  stay free of bash-only syntax (arrays, `[[ ]]`, `&>`) so it runs correctly
  under both `dash` (Debian's `/bin/sh`) and busybox `ash` (Alpine's
  `/bin/sh`) without needing `bash` installed at all. Build args via
  `set -- ... ; exec ... "$@"`, not a bash array. There's no `bashio`
  fallback branch (removed) -- this project never builds from an HA base
  image (see "No `build.yaml`" above), so `bashio` is never actually present;
  the plain `jq`-reads-`/data/options.json` path already covers both HA app
  installs and standalone Docker. A previous version of this script detected
  bashio via `command -v bashio &> /dev/null`, which is silently misparsed by
  `dash` (not the bash-only "redirect both stdout+stderr" meaning) and would
  take the wrong branch -- if you ever reintroduce shell-tool-presence
  detection here, use POSIX `> /dev/null 2>&1`, never `&>`.
- **`Dockerfile.alpine` is an experimental side build, not the published
  image**: `Dockerfile` (glibc, `python:3.12-slim-bookworm`) is still what CI
  builds and publishes to `ghcr.io`; `Dockerfile.alpine` exists purely so an
  Alpine build can be evaluated without any risk to the working setup. A
  prior investigation (see git history / earlier session notes) ruled Alpine
  out because `onnxruntime` had zero musllinux PyPI wheels -- that's no
  longer the blocker: Alpine's own `community` repo ships native musl builds
  of `onnxruntime`/`numpy`/`uv` (`py3-onnxruntime`, `py3-numpy`, `uv` apk
  packages), which is what makes this possible at all now. Known quirks if
  touching this file:
  - **`espeakng_loader` needs a shim** (`alpine/espeakng_loader/`):
    `blue_onnx/__init__.py` unconditionally imports the real PyPI
    `espeakng_loader` package and wires phonemizer-fork to its bundled
    library via `EspeakWrapper.set_library()`/`set_data_path()` -- but that
    bundled library is glibc-only (no musllinux wheel, and building from
    source doesn't produce a usable binary either). The shim's
    `get_library_path()`/`get_data_path()` point at Alpine's own
    `espeak-ng` apk package instead (`/usr/lib/libespeak-ng.so.1`,
    `/usr/share/espeak-ng-data`). Confirmed the real apk `espeak-ng` package
    is needed here (unlike the main Dockerfile, which doesn't need it at all
    since it uses the real `espeakng_loader`'s bundled glibc library).
  - **`py3-onnxruntime`/`py3-numpy` are installed only in the builder stage**,
    for their Python files (copied into the runtime stage). The runtime
    stage installs the underlying C libraries directly (`onnxruntime`,
    `openblas` -- no `py3-` prefix) instead of re-installing the `py3-`
    wrapper packages, which would otherwise duplicate the entire
    site-packages tree the builder's `COPY` already brings over.
  - **Dead-weight cleanup (`sympy`/`mpmath`/numpy's test suite) must be the
    LAST step in the builder stage**, not right after `apk add`: Alpine's
    `py3-onnxruntime` hard-depends on `py3-sympy` (mirroring PyPI
    onnxruntime's own optional "symbolic" extra, unused here), and it ships
    an old-style `.egg-info` with no proper `Requires-Dist` metadata --
    neither `pip` nor `uv` can tell the already-installed copy satisfies
    `renikud-onnx`'s plain `onnxruntime>=1.24.2` requirement, so both
    silently re-resolve and reinstall `sympy` from PyPI the moment that
    later install step runs, undoing an earlier cleanup pass.
  - `protoc`/`libprotoc` (the protobuf *compiler*, not the
    `libprotobuf`/`libprotobuf-lite` runtime libraries onnxruntime actually
    needs) get pulled into the runtime stage's apk package set for reasons
    not worth chasing through the resolver -- stripped post-install the same
    way as every other confirmed-dead package in this project's Dockerfiles.
  - No `bash` needed either, since `run.sh` is POSIX `sh` (see below) --
    Alpine's built-in busybox `ash` runs it directly.
  - Verified end-to-end (real synthesis, all default languages plus Hebrew):
    ~285 MB, smaller than the main Dockerfile's ~377 MB. Not yet verified:
    the `ENABLE_VOICE_CLONING` build arg (main Dockerfile's librosa/
    scikit-learn/sympy opt-in path) -- would need the same apk/pip split
    treatment as everything else here if ever added.
