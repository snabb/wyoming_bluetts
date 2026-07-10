"""Tests for model download integrity checks."""

import hashlib
from pathlib import Path

import huggingface_hub
from wyoming_bluetts import models


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_bundle_is_complete_rejects_corrupt_file(tmp_path, monkeypatch):
    payload = b"expected model"
    monkeypatch.setattr(models, "CORE_BUNDLE_FILES", ["model.onnx"])
    monkeypatch.setattr(models, "MODEL_FILE_SHA256", {"model.onnx": _sha256(payload)})

    (tmp_path / "model.onnx").write_bytes(b"corrupt")

    assert models.bundle_is_complete(tmp_path, include_cloning=False) is False


def test_model_bundle_download_uses_pinned_revision_and_replaces_corruption(
    tmp_path, monkeypatch
):
    payload = b"valid model"
    target = tmp_path / "model.onnx"
    target.write_bytes(b"corrupt")
    monkeypatch.setattr(models, "CORE_BUNDLE_FILES", [target.name])
    monkeypatch.setattr(models, "MODEL_FILE_SHA256", {target.name: _sha256(payload)})
    revisions: list[str] = []

    def fake_snapshot_download(*, revision: str, **_kwargs):
        revisions.append(revision)
        assert not target.exists()
        target.write_bytes(payload)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)

    models.ensure_model_bundle(tmp_path, include_cloning=False)

    assert revisions == [models.BLUETTS_REVISION]
    assert target.read_bytes() == payload


def test_renikud_download_replaces_corrupt_file_atomically(tmp_path, monkeypatch):
    payload = b"valid renikud model"
    target = tmp_path / models.RENIKUD_FILENAME
    target.write_bytes(b"corrupt")
    monkeypatch.setattr(models, "RENIKUD_SHA256", _sha256(payload))

    def fake_urlretrieve(_url: str, filename: str):
        Path(filename).write_bytes(payload)

    monkeypatch.setattr(models.urllib.request, "urlretrieve", fake_urlretrieve)

    assert models.ensure_renikud_model(tmp_path) is True
    assert target.read_bytes() == payload


def test_renikud_rejects_download_with_wrong_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "RENIKUD_SHA256", _sha256(b"expected"))

    def fake_urlretrieve(_url: str, filename: str):
        Path(filename).write_bytes(b"wrong")

    monkeypatch.setattr(models.urllib.request, "urlretrieve", fake_urlretrieve)

    assert models.ensure_renikud_model(tmp_path) is False
    assert not (tmp_path / models.RENIKUD_FILENAME).exists()
