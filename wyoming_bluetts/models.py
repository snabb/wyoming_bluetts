"""Auto-download logic for BlueTTS's ONNX model bundle and the optional Hebrew G2P model.

Neither blue-onnx (the BlueTTS inference package) nor BlueTTS itself download model
weights automatically -- users are expected to run `hf download` (and, for Hebrew,
`wget`) by hand first. This module does that for the server, so a fresh install
works out of the box.
"""

import logging
import os
import tempfile
import urllib.request
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

BLUETTS_REPO_ID = "notmax123/blue-onnx-v2"

# The core 4 inference graphs + runtime config. vocab.json is deliberately NOT
# listed here: it ships inside the blue-onnx pip package itself (see
# blue_onnx.load_text_processor(), which hardcodes a path relative to the
# installed package), not in this Hugging Face repo.
CORE_BUNDLE_FILES = [
    "text_encoder.onnx",
    "vector_estimator.onnx",
    "vocoder.onnx",
    "duration_predictor.onnx",
    "tts.json",
]

# Only needed for zero-shot voice cloning (blue_onnx.style.VoiceStyleExtractor,
# ~118 MB). Skipped when that module isn't importable -- the default Docker
# image (see Dockerfile's ENABLE_VOICE_CLONING) -- since downloading them there
# would be pure waste: the code to use them isn't even present.
CLONING_BUNDLE_FILES = [
    "codec_encoder.onnx",
    "style_encoder.onnx",
    "duration_style_encoder.onnx",
]

RENIKUD_URL = "https://huggingface.co/thewh1teagle/renikud/resolve/main/model.onnx"
RENIKUD_FILENAME = "model.onnx"


def _required_files(include_cloning: bool) -> list[str]:
    return CORE_BUNDLE_FILES + (CLONING_BUNDLE_FILES if include_cloning else [])


def bundle_is_complete(models_dir: Path, include_cloning: bool) -> bool:
    """Return True if every required file (core, plus cloning if enabled) exists."""
    return all(
        (models_dir / name).is_file() for name in _required_files(include_cloning)
    )


def ensure_model_bundle(models_dir: Path, include_cloning: bool) -> None:
    """Download the BlueTTS ONNX bundle into models_dir if it isn't already there.

    ``include_cloning`` should reflect whether ``blue_onnx.style`` is actually
    importable in this build -- downloading the cloning-only graphs when it
    isn't would just waste disk space on files the code can't use.

    Raises RuntimeError if the bundle is still incomplete after downloading --
    the caller treats this as fatal, since the server cannot serve any request
    without these files.
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    required = _required_files(include_cloning)

    if bundle_is_complete(models_dir, include_cloning):
        _LOGGER.info("BlueTTS model bundle already present in %s", models_dir)
        return

    _LOGGER.info(
        "Downloading BlueTTS model bundle from %s to %s (first run only)...",
        BLUETTS_REPO_ID,
        models_dir,
    )
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=BLUETTS_REPO_ID,
        repo_type="model",
        local_dir=str(models_dir),
        allow_patterns=required,
    )

    if not bundle_is_complete(models_dir, include_cloning):
        missing = [name for name in required if not (models_dir / name).is_file()]
        raise RuntimeError(
            f"BlueTTS model bundle is incomplete after download; missing: {missing}"
        )

    _LOGGER.info("BlueTTS model bundle downloaded successfully")


def ensure_blue_onnx_vocab() -> None:
    """Copy the bundled vocab.json next to the installed blue_onnx package.

    blue_onnx.load_text_processor() hardcodes its tokenizer vocab path as
    <installed-package-dir>/../vocab.json, but no build of blue-onnx actually
    ships that file in its wheel -- a packaging gap upstream, verified against
    both the published PyPI 0.2.4 wheel and a from-source build of the exact
    git commit pinned in pyproject.toml: vocab.json exists in BlueTTS's source
    tree but the build backend doesn't include it in either case. We vendor
    our own copy (identical content, from BlueTTS's own repo) and place it
    where blue_onnx expects to find it. Needed unconditionally (all
    languages), not just for Hebrew.
    """
    import blue_onnx

    target = Path(blue_onnx.__file__).resolve().parent.parent / "vocab.json"
    if target.is_file():
        return
    source = Path(__file__).parent / "vocab.json"
    target.write_bytes(source.read_bytes())


def renikud_is_present(models_dir: Path) -> bool:
    """Return True if the Hebrew G2P (Renikud) model is already present."""
    return (models_dir / RENIKUD_FILENAME).is_file()


def ensure_renikud_model(models_dir: Path) -> bool:
    """Best-effort download of the Hebrew G2P model into models_dir.

    Returns True if the model is present (already there or freshly downloaded),
    False if the download failed -- the caller treats this as a soft failure and
    drops Hebrew from the advertised languages rather than crashing the server.
    """
    if renikud_is_present(models_dir):
        return True

    _LOGGER.info("Downloading Hebrew G2P (Renikud) model from %s...", RENIKUD_URL)
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / RENIKUD_FILENAME

    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(models_dir), suffix=".part")
        os.close(fd)
        try:
            urllib.request.urlretrieve(RENIKUD_URL, tmp_path)
            os.replace(tmp_path, target)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
    except Exception:
        _LOGGER.exception(
            "Failed to download Hebrew G2P model; Hebrew will be unavailable"
        )
        return False

    _LOGGER.info("Hebrew G2P model downloaded successfully")
    return True
