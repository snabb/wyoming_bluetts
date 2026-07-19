"""Tests for model download integrity checks."""

import hashlib
from pathlib import Path

import huggingface_hub
import pytest
from wyoming_bluetts import models


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_bundle_is_complete_ignores_content(tmp_path, monkeypatch):
    # Existence only: a present-but-corrupt file still counts as complete.
    # Startup trusts disk contents rather than re-hashing hundreds of MB on
    # every boot -- SHA-256 is only checked around the download itself.
    payload = b"expected model"
    monkeypatch.setattr(models, "CORE_BUNDLE_FILES", ["model.onnx"])
    monkeypatch.setattr(models, "MODEL_FILE_SHA256", {"model.onnx": _sha256(payload)})

    (tmp_path / "model.onnx").write_bytes(b"corrupt")

    assert models.bundle_is_complete(tmp_path, include_cloning=False) is True


def test_bundle_is_complete_false_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "CORE_BUNDLE_FILES", ["model.onnx"])
    monkeypatch.setattr(models, "MODEL_FILE_SHA256", {"model.onnx": _sha256(b"x")})

    assert models.bundle_is_complete(tmp_path, include_cloning=False) is False


def test_ensure_model_bundle_skips_download_when_already_present(tmp_path, monkeypatch):
    # Even a corrupt-but-present file is trusted at startup, not re-verified.
    payload = b"valid model"
    target = tmp_path / "model.onnx"
    target.write_bytes(b"corrupt")
    monkeypatch.setattr(models, "CORE_BUNDLE_FILES", [target.name])
    monkeypatch.setattr(models, "MODEL_FILE_SHA256", {target.name: _sha256(payload)})

    def fail_snapshot_download(**_kwargs):
        raise AssertionError("should not download when the file already exists")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fail_snapshot_download)

    models.ensure_model_bundle(tmp_path, include_cloning=False)

    assert target.read_bytes() == b"corrupt"


def test_model_bundle_download_uses_pinned_revision_and_verifies_hash(
    tmp_path, monkeypatch
):
    payload = b"valid model"
    target = tmp_path / "model.onnx"
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


def test_model_bundle_download_rejects_wrong_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "CORE_BUNDLE_FILES", ["model.onnx"])
    monkeypatch.setattr(
        models, "MODEL_FILE_SHA256", {"model.onnx": _sha256(b"expected")}
    )

    def fake_snapshot_download(**_kwargs):
        (tmp_path / "model.onnx").write_bytes(b"wrong")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)

    with pytest.raises(RuntimeError):
        models.ensure_model_bundle(tmp_path, include_cloning=False)


def test_renikud_is_present_ignores_content(tmp_path):
    # Existence only, same rationale as bundle_is_complete: trust disk
    # contents at startup instead of re-hashing on every boot.
    (tmp_path / models.RENIKUD_FILENAME).write_bytes(b"corrupt")

    assert models.renikud_is_present(tmp_path) is True


def test_ensure_renikud_model_skips_download_when_already_present(
    tmp_path, monkeypatch
):
    (tmp_path / models.RENIKUD_FILENAME).write_bytes(b"corrupt")

    def fail_urlretrieve(_url: str, _filename: str):
        raise AssertionError("should not download when the file already exists")

    monkeypatch.setattr(models.urllib.request, "urlretrieve", fail_urlretrieve)

    assert models.ensure_renikud_model(tmp_path) is True
    assert (tmp_path / models.RENIKUD_FILENAME).read_bytes() == b"corrupt"


def test_renikud_download_verifies_hash(tmp_path, monkeypatch):
    payload = b"valid renikud model"
    target = tmp_path / models.RENIKUD_FILENAME
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
