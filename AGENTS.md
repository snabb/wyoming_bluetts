# Agent Guidelines

- Use `uv` for all dependency management (`uv add`, `uv run`). Never use
  `requirements.txt`, and prefer `uv pip` over bare `pip` anywhere a command
  is needed.
- Python 3.12+ (required by `blue-onnx`). Use modern type annotations
  (`list`, `dict`, `X | None`).
- Before committing: run `prek run --all-files` and `uv run -m pytest`. All
  hooks and tests must pass.
- This repo's default branch is `master`, not `main`. `.github/workflows/on-merge.yml`
  and `on-pr.yml` must trigger on `master` -- if this drifts back to `main`
  the ghcr.io image build/push silently never runs again.
- This is a Wyoming protocol TTS server for Home Assistant. Changes must
  maintain compatibility with the Wyoming protocol and Home Assistant's app
  (Supervisor add-on) system.
- **Terminology**: Home Assistant renamed "add-ons" to "apps" in user-facing
  text. Use "app" in README/DOCS.md prose. The underlying Supervisor manifest
  files (`config.yaml`, `repository.yaml`) and the developers.home-assistant.io
  `/docs/add-ons/...` doc URLs are unchanged -- don't rename those files or
  "fix" those URLs.
- **No `build.yaml`, on purpose**: Supervisor deprecated it in favor of the
  Dockerfile handling `FROM`/build args itself. Our Dockerfiles already
  hardcode their `FROM` lines and never consumed `BUILD_FROM` -- don't
  re-add `build.yaml`.
- The project wraps [BlueTTS](https://github.com/maxmelichov/BlueTTS)
  (`blue-onnx` package). Voice handling (preset + custom + cloning) is in
  `wyoming_bluetts/handler.py`; model auto-download is in
  `wyoming_bluetts/models.py`.
- **Streaming design**: BlueTTS's `tts(...)` call is blocking and returns one
  whole-clip buffer per call -- no native frame-by-frame generator.
  `handler._iter_audio_pcm_chunks()` recovers incremental delivery by
  splitting text into sentence/paragraph chunks (`blue_onnx.chunk_text`) and
  calling the engine once per chunk. Do not "simplify" this back into a
  single whole-text call -- that reintroduces the latency streaming exists to
  avoid. The server advertises `supports_synthesize_streaming=True`, so a
  `SynthesizeStopped` event must always follow `AudioStop` (including on the
  exception path) -- omitting it hangs Home Assistant's streaming client
  forever.
- **Renikud CWD workaround**: `blue_onnx.TextProcessor.__init__` only
  auto-discovers the Hebrew G2P model via a hardcoded relative check
  (`os.path.exists("model.onnx")` in the process's CWD), and
  `load_text_to_speech()` has no way to pass a custom path. `__main__.py`
  works around this by placing `model.onnx` in `models_dir` and `chdir`-ing
  there before loading the engine. Intentional -- don't remove the `chdir`.
- **`blue-onnx` is pinned to a git commit, not a PyPI release**: the
  published PyPI wheel (`0.2.4`) is missing `blue_onnx.style` (zero-shot wav
  cloning, needs `>=0.2.5`), so `pyproject.toml` points `blue-onnx` at
  `git+https://github.com/maxmelichov/BlueTTS.git` pinned to a commit via
  `[tool.uv.sources]`. Switch back to a plain PyPI version constraint once a
  release with `blue_onnx.style` ships, re-testing
  `models.ensure_blue_onnx_vocab()` against it (the vocab.json bug may or may
  not be fixed there too).
- **`vocab.json` packaging workaround**: no build of `blue-onnx` ships
  `vocab.json` in its wheel, even though `blue_onnx.load_text_processor()`
  hardcodes the tokenizer vocab path as `<installed-package-dir>/../vocab.json`.
  `wyoming_bluetts/vocab.json` vendors a copy and
  `models.ensure_blue_onnx_vocab()` installs it next to `blue_onnx` at
  startup. Re-check whenever the `blue-onnx` pin is bumped.
- **Two Dockerfiles, both built and published by CI, two roles**: `Dockerfile`
  (Alpine) is the default -- tagged `latest`/`<version>`/`<short-sha>`, used
  by the Home Assistant app, pulled by `docker-compose.yml`. It cannot
  support zero-shot `.wav` voice cloning at all. `Dockerfile.cloning` (glibc,
  `python:3.12-slim-trixie`) has cloning on by default, published as
  `latest-cloning`/`<version>-cloning`/`<short-sha>-cloning`. Still not the
  default: not used by the HA app, not what `docker-compose.yml` pulls unless
  you change the tag. Keep both working; don't let one regress while
  changing the other.
- **CI has a real smoke test, not just `pytest`**: `job-docker.yml`'s
  `smoke-test` matrix pulls every variant/architecture image by its untagged
  digest on a native runner, waits for the container's own healthcheck, then
  runs `.github/scripts/smoke_test.py` -- a real `Synthesize` request over the
  Wyoming protocol, asserting non-empty audio comes back. Public tags are
  created only after all four smoke tests pass. This is the only place that
  verifies a built image actually boots and synthesizes; `job-test.yml`'s
  `pytest` run never touches Docker. If you change `run.sh`, the healthcheck,
  or the synthesis path, this is what would catch a regression unit tests
  can't see.
- **Voice cloning support is driven entirely by whether `blue_onnx.style` is
  importable, not by which Dockerfile you're looking at**: `handler.py` and
  `__main__.py` both **soft-import** it (`try`/`except ImportError`) --
  `style_extractor` can be `None` at runtime, and `load_voice()`'s `.wav`
  branch must keep handling that gracefully (log + fall back to the default
  voice). Local `uv sync`/dev installs always have the full dependency set,
  so `style_extractor` is only ever `None` in a Docker image built without
  cloning support, not in dev/tests. Keep new code touching `style_extractor`
  `None`-safe.
- **Model download mirrors the same cloning on/off split**: `models.py` keeps
  `CORE_BUNDLE_FILES` (always downloaded) separate from `CLONING_BUNDLE_FILES`
  (the 3 zero-shot-cloning ONNX graphs, ~118 MB), fetching the latter only
  when `ensure_model_bundle(..., include_cloning=...)` is called with
  `include_cloning=True` (`__main__.py` passes
  `VoiceStyleExtractor is not None`). Both the BlueTTS and Renikud downloads
  are pinned to immutable Hugging Face revisions and verified with SHA-256;
  update the revision and every affected digest together when changing model
  bundles.
- **Hebrew ("he") is not in the default `--languages`**: needs an extra
  ~20 MB G2P (renikud) model download most installs don't need. Still fully
  supported -- pass `--languages ...,he` (or the HA app's `languages` option)
  to enable it. Keep `DEFAULT_LANGUAGES` in `__main__.py`, `config.yaml`'s
  `options.languages`, `run.sh`'s fallback defaults, and the
  `docker-compose.yml` example in sync if this changes again.
- **`run.sh` is POSIX `sh`, not bash**: shebang is `#!/bin/sh`, and it must
  stay free of bash-only syntax (arrays, `[[ ]]`, `&>`) so it runs under both
  `dash` (Debian) and busybox `ash` (Alpine) without needing `bash`
  installed. Build args via `set -- ... ; exec ... "$@"`, not a bash array.
  There's no `bashio` fallback branch -- this project never builds from an
  HA base image, so `bashio` is never present; the plain
  `jq`-reads-`/data/options.json` path covers both HA app installs and
  standalone Docker. Never use `&>` for tool-presence detection under `sh`
  (dash misparses it) -- use POSIX `> /dev/null 2>&1`.
- **`Dockerfile` (Alpine) is the published/default image**. `config.yaml`'s
  `image:` field points Supervisor at `ghcr.io/snabb/wyoming_bluetts` and
  auto-appends `:<version>` from `config.yaml`'s own `version` field, so
  **every version bump there must have a matching image tag already
  published** (CI does this automatically on push) -- bump `config.yaml`'s
  version in the same commit/push as everything else, never ahead of it).
  Keep that version and `wyoming_bluetts.__version__` equal to
  `pyproject.toml`'s version; `tests/test_versions.py` gates image publishing
  on this invariant.
  Known quirks if touching this file:
  - **`espeakng_loader` needs a shim** (`alpine/espeakng_loader/`):
    `blue_onnx/__init__.py` unconditionally imports the real PyPI
    `espeakng_loader` package and wires phonemizer-fork to its bundled
    library -- but that bundled library is glibc-only. The shim's
    `get_library_path()`/`get_data_path()` point at Alpine's own
    `espeak-ng` apk package instead (`/usr/lib/libespeak-ng.so.1`,
    `/usr/share/espeak-ng-data`), which is why this is the one Dockerfile
    that needs the real apk `espeak-ng` package installed.
  - **`py3-onnxruntime`/`py3-numpy` are installed only in the builder stage**,
    for their Python files (copied into the runtime stage). The runtime
    stage installs the underlying C libraries directly (`onnxruntime`,
    `openblas` -- no `py3-` prefix) instead of re-installing the `py3-`
    wrapper packages, which would otherwise duplicate the site-packages tree
    the builder's `COPY` already brings over.
  - **Dead-weight cleanup (`sympy`/`mpmath`/numpy's test suite) must be the
    LAST step in the builder stage**, not right after `apk add`: Alpine's
    `py3-onnxruntime` hard-depends on `py3-sympy` and ships an old-style
    `.egg-info` with no proper `Requires-Dist` metadata, so `uv`/`pip` can't
    tell the already-installed copy satisfies `renikud-onnx`'s
    `onnxruntime>=1.24.2` requirement and silently reinstalls `sympy` the
    moment a later install step runs, undoing an earlier cleanup pass.
  - `protoc`/`libprotoc` get pulled into the runtime stage's apk package set
    even though only the `libprotobuf`/`libprotobuf-lite` runtime libraries
    are needed -- stripped post-install like other confirmed-dead packages.
  - **Cannot support `ENABLE_VOICE_CLONING`, and this is not a quick fix**:
    `scipy`/`scikit-learn` are available as native apk packages, but
    `numba`/`llvmlite` are not, and a from-source `llvmlite` build fails
    outright even with Alpine's own LLVM toolchain installed. Making cloning
    work here would need either patching llvmlite's build for musl or
    replacing librosa's numba-JIT mel-spectrogram extraction with a pure
    NumPy/SciPy implementation -- a real separate project. This is why
    `Dockerfile.cloning` exists as a separate file rather than an
    `ENABLE_VOICE_CLONING` arg on this one.
  - **Known tradeoff, accepted deliberately**: `onnxruntime`/`openblas` here
    come from Alpine's community-maintained rebuild, not the official
    upstream PyPI artifact `Dockerfile.cloning` uses. Made primary anyway for
    the size win; if that trust/maintenance tradeoff ever stops being
    acceptable, revert by swapping the two Dockerfiles back.

- **`Dockerfile.cloning` (glibc, `python:3.12-slim-trixie`) is the
  alternative for zero-shot voice cloning** -- built and published by CI
  (`latest-cloning`/`<version>-cloning` tags), but still not the default.
  `ENABLE_VOICE_CLONING` defaults to `true` here (opposite of the Alpine
  `Dockerfile`'s permanent "unsupported"); pass
  `--build-arg ENABLE_VOICE_CLONING=false` for this glibc build without it.
  Notes specific to this file:
  - **`onnx`/`onnxslim` removed post-install**: blue-onnx hard-requires them
    (for `exports/` conversion tooling this project never calls) with no
    resolver flag to skip installing them. Confirmed unused: blue_onnx's only
    two source files only ever do `import onnxruntime as ort`; onnxruntime
    itself doesn't depend on `onnx`. `ml_dtypes` is `onnx`/`onnxslim`'s own
    now-orphaned dependency, removed alongside them. Re-verify whenever the
    blue-onnx pin bumps.
  - **The `ENABLE_VOICE_CLONING=false` cleanup path removes**
    librosa/numba/llvmlite/scipy/scikit-learn/sympy plus their own now-
    orphaned dependencies (confirmed via `uv.lock` reverse-dependency check
    that nothing else needs them). Must be the LAST builder-stage step, not
    right after install, for the same reason as the Alpine Dockerfile's
    sympy cleanup: a later install step can silently reintroduce a package an
    earlier cleanup pass removed.
  - **`pip` must be removed in the runtime stage, not the builder stage**:
    the runtime base image (`python:3.12-slim-trixie`) ships its own
    pre-installed `pip` via `ensurepip`, unrelated to anything the builder
    stage installs, and `COPY --from=builder` merges into this image's
    already-existing site-packages rather than replacing it -- a
    builder-stage removal never touches the runtime base's own copy.
  - **No `espeak-ng` apt package needed**: `blue_onnx/__init__.py` wires
    phonemizer-fork directly to `espeakng_loader`'s own bundled
    `libespeak-ng.so`/`espeak-ng-data`, and phonemizer-fork's espeak backend
    has no subprocess/CLI fallback anywhere.
