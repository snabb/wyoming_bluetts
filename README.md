# Wyoming BlueTTS

[Wyoming protocol](https://github.com/OHF-Voice/wyoming) server for
[BlueTTS](https://github.com/maxmelichov/BlueTTS) (the `blue-onnx` ONNX
inference package), for use as a [Home Assistant](https://www.home-assistant.io/)
text-to-speech provider.

Inspired by and partially based on
[wyoming_pocket_tts](https://github.com/araa47/wyoming_pocket_tts), which
this project follows for its overall structure (pip package + Home Assistant
app packaging, event handler design).

## Features

- **Multilingual**: English, Spanish, German, Italian, Hebrew. Voices are
  language-independent style embeddings, so any voice works with any
  language.
- **Incremental audio streaming**: long replies are split into sentence/paragraph
  chunks and streamed to the client as each chunk finishes synthesizing,
  rather than waiting for the entire reply (`supports_synthesize_streaming`).
  Home Assistant plays audio as it arrives instead of after the whole clip is
  ready.
- **Zero-shot voice cloning** from a short reference `.wav` clip (see
  [Voices](#voices) below) — not available in the default (Alpine) image;
  build [`Dockerfile.cloning`](#docker) yourself if you need it.
- **CPU only**: ONNX Runtime, no PyTorch dependency, no GPU required.
- **Models download automatically** on first start.
- Ships both as a pip-installable Python package and a Home Assistant app.

## Quick start

### Home Assistant app

Settings → Apps → Install app → ⋮ (three dots) → Repositories → Add this
repository's URL, then install "Wyoming BlueTTS" from the store. It
auto-discovers into Home Assistant via the Wyoming protocol.

### Standalone (uv)

```bash
git clone https://github.com/snabb/wyoming_bluetts.git
uv tool install ./wyoming_bluetts
wyoming-bluetts --voices female1 --debug
```

**Must be installed with `uv`, not plain `pip`.** This project pins its
`blue-onnx` dependency to a specific git commit via `[tool.uv.sources]`
(see [AGENTS.md](AGENTS.md)), because the published PyPI `blue-onnx` wheel is
missing required files as of this writing. `uv` honors that pin when building
from this project's `pyproject.toml`; plain `pip` has no equivalent mechanism
and would silently install the broken PyPI `blue-onnx` instead.

### Docker

```bash
docker run --rm -p 10200:10200 \
  -v ./models:/data/models \
  -v ./voices:/share/tts-voices \
  ghcr.io/snabb/wyoming_bluetts:latest
```

See [docker-compose.yml](docker-compose.yml) for a persistent deployment
example, including binding to a specific interface (e.g. a WireGuard IP) if
Home Assistant reaches this host over a VPN. Add it in Home Assistant via
Settings → Devices & Services → Add integration → "Wyoming Protocol".

The published image (and the Home Assistant app, which builds from the same
`Dockerfile`) is Alpine-based — smaller than a typical glibc image, but it
can't support zero-shot `.wav` voice cloning: that feature needs a
`librosa`/`numba`/`llvmlite`/`scipy`/`scikit-learn` dependency chain, and
`numba`/`llvmlite` don't build on musl (see [AGENTS.md](AGENTS.md)).
Precomputed style JSON custom voices work in every build regardless.

If you need cloning, build [`Dockerfile.cloning`](Dockerfile.cloning)
instead (glibc-based, cloning on by default):

```bash
docker build -f Dockerfile.cloning -t wyoming-bluetts:cloning .
```

## Configuration (CLI flags)

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Host to bind to |
| `--port` | `10200` | Port to bind to |
| `--voices` | *(empty)* | Comma-separated voices to preload + advertise; empty = advertise all, load on demand |
| `--voices-dir` | `/share/tts-voices` | Folder for custom voice style JSON / wav samples |
| `--models-dir` | `/data/models` | Folder for the auto-downloaded ONNX model bundle |
| `--languages` | `en,es,de,it` | Comma-separated languages to advertise |
| `--default-language` | `en` | Language used when a request doesn't resolve one |
| `--total-step` | `5` | Flow-matching diffusion steps (quality/speed tradeoff) |
| `--cfg-scale` | `4.0` | Classifier-free guidance scale |
| `--speed` | `1.0` | Speech speed multiplier |
| `--debug` | off | Verbose logging |

## Voices

Two built-in presets ship with this package: `female1`, `male1` (vendored
from [BlueTTS's `voices/`](https://github.com/maxmelichov/BlueTTS/tree/main/voices)).

Custom voices go in `--voices-dir` (default `/share/tts-voices`):

- A precomputed style JSON (e.g. exported with BlueTTS's own tooling) — works
  today.
- A clean, 5-15 second mono reference `.wav` clip, cloned automatically on
  first use via zero-shot voice conversion, then cached to
  `<voices-dir>/.bluetts_cache/<name>.json` so cloning only runs once. Only
  available in a build from [`Dockerfile.cloning`](Dockerfile.cloning) (see
  [Docker](#docker)) — the published (Alpine) image and the Home Assistant
  app can't support it at all, and log a clear warning and fall back to the
  default voice if a `.wav`-only voice is requested.

## Languages

`en`, `es`, `de`, `it` by default. `he` (Hebrew) is also supported but not
enabled by default — it needs an extra ~20 MB grapheme-to-phoneme model
(`renikud`); add it via `--languages en,es,de,it,he` (or your own subset) to
enable it, and it'll download automatically alongside the main model bundle.
If that download fails, Hebrew is dropped from the advertised languages and
the rest still work.

## Limitations

- BlueTTS's underlying `tts()` call synthesizes one sentence/paragraph chunk
  at a time, not frame-by-frame — so streaming here is sentence-level, not
  as fine-grained as engines with a native audio-frame streaming API. Still,
  playback of a multi-sentence reply starts after the first chunk rather than
  waiting for the whole thing.
- CPU only; `use_gpu=True` is not implemented upstream yet.

## Development

```bash
uv sync --all-extras --dev
prek run --all-files
uv run -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## Acknowledgments

Structure and packaging approach follow
[wyoming_pocket_tts](https://github.com/araa47/wyoming_pocket_tts) by
[araa47](https://github.com/araa47) (MIT), the template this project was
built from.

## License

MIT. Uses [BlueTTS](https://github.com/maxmelichov/BlueTTS) (MIT) and the
[Wyoming protocol](https://github.com/OHF-Voice/wyoming) (MIT).
