"""Auto-download logic for BlueTTS's ONNX model bundle and the optional Hebrew G2P model.

Neither blue-onnx (the BlueTTS inference package) nor BlueTTS itself download model
weights automatically -- users are expected to run `hf download` (and, for Hebrew,
`wget`) by hand first. This module does that for the server, so a fresh install
works out of the box.
"""

import hashlib
import logging
import os
import tempfile
import urllib.request
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

BLUETTS_REPO_ID = "notmax123/blue-onnx-v2"
BLUETTS_REVISION = "45dc85f1ac045ea62458a7492c5ae387610ac0af"

# The core 4 inference graphs + runtime config. vocab.json is deliberately not
# listed here because this project vendors it separately; it is not part of the
# Hugging Face model repository.
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

MODEL_FILE_SHA256 = {
    "text_encoder.onnx": "9f80f87093067ee2300343133b0456e36d705eea31f523f9d3dc9f4c5e212db1",
    "vector_estimator.onnx": "8c333ef2eb0c075136384eaa9608a374230f1b26e8181097467b803078095a5a",
    "vocoder.onnx": "f4fbb6f60dec035cd8071883e021ac2cd3eee62630e42174492fd2d1f39976db",
    "duration_predictor.onnx": "1fb897a3f1c5f4132ffba3e56c5792f8c03783e2f478d825774514da668af161",
    "tts.json": "9afc8622ee9a40adff8befdc6bdfcb3c008f8caed9750febe870e53f47a73a0a",
    "codec_encoder.onnx": "afca5068649df31d985c9d2fb3066dee1c9151704ee02f12c9ce49c89d245331",
    "style_encoder.onnx": "b37b5dce98ea925a8c1307a93d5598096287feaa885b3b864a20a0bdc8178f7f",
    "duration_style_encoder.onnx": "232e8fe22f978ad863844624d067becee788ca519e447ae26a29efbff9a2bbfa",
}

RENIKUD_REVISION = "b80cab616c972b6a87de86044f50b597ee2c53fd"
RENIKUD_URL = (
    "https://huggingface.co/thewh1teagle/renikud/resolve/"
    f"{RENIKUD_REVISION}/model.onnx"
)
RENIKUD_FILENAME = "model.onnx"
RENIKUD_SHA256 = "8b881a3a8f00283d86c6d1feea44d37e09c1ea6609a4de7d820d937e7f3dbbca"


def _required_files(include_cloning: bool) -> list[str]:
    return CORE_BUNDLE_FILES + (CLONING_BUNDLE_FILES if include_cloning else [])


def _file_matches_sha256(path: Path, expected: str) -> bool:
    if not path.is_file():
        return False

    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def bundle_is_complete(models_dir: Path, include_cloning: bool) -> bool:
    """Return True if every required file exists and matches its pinned hash."""
    return all(
        _file_matches_sha256(models_dir / name, MODEL_FILE_SHA256[name])
        for name in _required_files(include_cloning)
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

    for name in required:
        path = models_dir / name
        if path.exists() and not _file_matches_sha256(path, MODEL_FILE_SHA256[name]):
            _LOGGER.warning("Removing invalid model file: %s", path)
            path.unlink()

    _LOGGER.info(
        "Downloading BlueTTS model bundle from %s to %s (first run only)...",
        BLUETTS_REPO_ID,
        models_dir,
    )
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=BLUETTS_REPO_ID,
        repo_type="model",
        revision=BLUETTS_REVISION,
        local_dir=str(models_dir),
        allow_patterns=required,
    )

    if not bundle_is_complete(models_dir, include_cloning):
        missing = [
            name
            for name in required
            if not _file_matches_sha256(models_dir / name, MODEL_FILE_SHA256[name])
        ]
        raise RuntimeError(
            "BlueTTS model bundle is incomplete or corrupt after download; "
            f"invalid files: {missing}"
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
    return _file_matches_sha256(models_dir / RENIKUD_FILENAME, RENIKUD_SHA256)


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
    if target.exists():
        _LOGGER.warning("Replacing invalid Hebrew G2P model: %s", target)

    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(models_dir), suffix=".part")
        os.close(fd)
        try:
            urllib.request.urlretrieve(RENIKUD_URL, tmp_path)
            if not _file_matches_sha256(Path(tmp_path), RENIKUD_SHA256):
                raise RuntimeError("Downloaded Hebrew G2P model failed SHA-256 check")
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
