import hashlib
import zipfile
from pathlib import Path

import pytest

from pipeline import homr_models

MODEL = "segmentation/segnet_1-abc.onnx"
CONTENT = b"the model homr reads pages with"


@pytest.fixture
def pinned(monkeypatch):
    """One small stand-in model in place of homr's three."""
    monkeypatch.setattr(homr_models, "MODELS", {MODEL: (hashlib.sha256(CONTENT).hexdigest(), len(CONTENT))})


def release_with(folder: Path, content: bytes) -> str:
    """A release holding the model's zip, as homr's does, and its address."""
    folder.mkdir()
    with zipfile.ZipFile(folder / "segnet_1-abc.zip", "w") as bundle:
        bundle.writestr("segnet_1-abc.onnx", content)
    return folder.as_uri() + "/"


def test_a_model_there_and_right_is_read_with(tmp_path, pinned):
    (tmp_path / "homr" / "segmentation").mkdir(parents=True)
    (tmp_path / "homr" / MODEL).write_bytes(CONTENT)
    homr_models.ensure(tmp_path / "homr", release=(tmp_path / "nowhere").as_uri() + "/")
    assert (tmp_path / "homr" / MODEL).read_bytes() == CONTENT


def test_a_model_there_that_differs_is_refused(tmp_path, pinned):
    """homr reads with whatever file is there, so one replaced or cut short was run as it was."""
    (tmp_path / "homr" / "segmentation").mkdir(parents=True)
    (tmp_path / "homr" / MODEL).write_bytes(CONTENT.upper())
    with pytest.raises(homr_models.ModelProblem):
        homr_models.ensure(tmp_path / "homr", release=release_with(tmp_path / "release", CONTENT))


def test_a_missing_model_is_fetched_and_kept_when_it_is_the_one_pinned(tmp_path, pinned):
    homr_models.ensure(tmp_path / "homr", release=release_with(tmp_path / "release", CONTENT))
    assert (tmp_path / "homr" / MODEL).read_bytes() == CONTENT
    assert [path.name for path in (tmp_path / "homr" / "segmentation").iterdir()] == ["segnet_1-abc.onnx"]


def test_a_download_that_is_not_the_model_pinned_is_refused_and_leaves_nothing(tmp_path, pinned):
    """homr downloaded its models' zips from its release and unpacked them with no check at all."""
    with pytest.raises(homr_models.ModelProblem):
        homr_models.ensure(tmp_path / "homr", release=release_with(tmp_path / "release", CONTENT.upper()))
    assert list((tmp_path / "homr" / "segmentation").iterdir()) == []


def test_the_models_pinned_are_the_ones_this_homr_reads_with():
    """A newer homr names other models: their hashes have to be pinned before it can read a page."""
    pytest.importorskip("homr")
    from homr.segmentation.config import segnet_path_onnx
    from homr.transformer.configs import default_config

    folder = homr_models.homr_folder().resolve()
    used = {segnet_path_onnx, default_config.filepaths.encoder_path, default_config.filepaths.decoder_path}
    assert {Path(path).resolve().relative_to(folder).as_posix() for path in used} == set(homr_models.MODELS)


def test_reading_with_homr_checks_its_models_first(tmp_path, monkeypatch):
    pytest.importorskip("homr")
    from pipeline import recognize

    def refuse() -> None:
        raise homr_models.ModelProblem("not the model pinned")

    monkeypatch.setattr(homr_models, "ensure", refuse)
    with pytest.raises(homr_models.ModelProblem):
        recognize.run_homr(tmp_path / "page.png", tmp_path)
