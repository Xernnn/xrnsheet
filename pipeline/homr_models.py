"""Check homr's models against pinned SHA-256s before a page is read with them, and fetch a missing one.

homr downloads its three models the first time it reads a page, as zips from its own GitHub release, and
checks nothing: a file replaced on that release, or one cut short, would simply be run. So run_homr asks
this module first. A model that's missing is fetched from the same release into a folder of its own, taken
out of its zip, and kept only if it's the size and SHA-256 pinned here. One already there that differs is
refused, and the page fails, rather than be read with it.

The names and hashes are homr 0.7.0's, as requirements.txt pins it. A newer homr names other models, which
tests/test_homr_models.py notices: read their hashes from a copy you trust and pin them here.
"""

import hashlib
import importlib.util
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RELEASE = "https://github.com/liebharc/homr/releases/download/onnx_checkpoints/"
# The models homr reads a page with on the CPU, by where they go in homr's folder: their SHA-256 and size.
MODELS = {
    "segmentation/segnet_308-3296ccd40960f90ca6ab9c035cca945675d30a0f.onnx": ("6ed36640db4ef5d223098b6d5efe4eda97c66b24a2c72faab8a018c749003a8d", 57311361),
    "transformer/encoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.onnx": ("4c16df852b3789f2676b0d49f0545dab0740e4005f7b472c5252add642f5d5eb", 52861122),
    "transformer/decoder_pytorch_model_396-f6feedb42ff90087d898b0941a55d040fa6b2903.onnx": ("3e10fd5ae52d0b86792721922fcd954c283a7ed365de7446425bdabe38f3e57d", 47299551),
}
CHUNK = 1 << 20


class ModelProblem(RuntimeError):
    """A model of homr's isn't the file pinned for it, or couldn't be fetched."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def homr_folder() -> Path:
    """Where homr is installed, found without importing it."""
    spec = importlib.util.find_spec("homr")
    if spec is None or spec.origin is None:
        raise ModelProblem("homr isn't installed")
    return Path(spec.origin).parent


def ensure(folder: Path | None = None, release: str = RELEASE) -> None:
    """Make sure each model is there and is the file pinned for it, fetching any that's missing."""
    folder = folder or homr_folder()
    for name, (sha256, size) in MODELS.items():
        path = folder / name
        if not path.exists():
            fetch(release + path.stem + ".zip", path, sha256, size)
        elif path.stat().st_size != size or sha256_of(path) != sha256:
            raise ModelProblem(f"{path} isn't the model pinned for homr; delete it to fetch it again")


def fetch(url: str, path: Path, sha256: str, size: int) -> None:
    """Download a model's zip, and keep the model in it only if it's the file pinned."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=f".{path.stem}.") as work:
        archive = Path(work) / "model.zip"
        try:
            with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as out:
                shutil.copyfileobj(response, out, CHUNK)
        except OSError as error:  # urllib's errors are OSErrors too.
            raise ModelProblem(f"couldn't download {url}: {error}") from error
        unpacked = Path(work) / path.name
        try:
            with zipfile.ZipFile(archive) as bundle:
                member = next((item for item in bundle.infolist() if Path(item.filename).name == path.name), None)
                if member is None or member.file_size != size:
                    raise ModelProblem(f"{url} doesn't hold the {path.name} pinned for homr")
                with bundle.open(member) as source, unpacked.open("wb") as out:
                    shutil.copyfileobj(source, out, CHUNK)
        except zipfile.BadZipFile as error:
            raise ModelProblem(f"{url} isn't a zip") from error
        if sha256_of(unpacked) != sha256:
            raise ModelProblem(f"the {path.name} in {url} isn't the model pinned for homr")
        os.chmod(unpacked, 0o644)
        os.replace(unpacked, path)
