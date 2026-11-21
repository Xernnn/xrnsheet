"""tools/fetch_models.py, which downloads the readers from the release models/models.json names.

Each test runs it on a copy of the repository's layout, fetching from a folder standing in for the
release, with no site packages, as it has to run before anything is installed.
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "fetch_models.py"
FILES = {"chord_reader.onnx": b"chords " * 1000, "note_reader.onnx": b"notes " * 1000, "chord_reader.pt": b"trained chords", "note_reader.pt": b"trained notes"}


@pytest.fixture
def checkout(tmp_path):
    """A checkout with the script and a models.json, and a release to fetch from."""
    release = tmp_path / "release"
    release.mkdir()
    for name, data in FILES.items():
        (release / name).write_bytes(data)
    (tmp_path / "tools").mkdir()
    shutil.copy(SCRIPT, tmp_path / "tools")
    (tmp_path / "models").mkdir()
    listing = {
        "release": "models-test",
        "url": release.as_uri() + "/",
        "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in FILES.items()},
    }
    (tmp_path / "models" / "models.json").write_text(json.dumps(listing), encoding="utf-8")
    return tmp_path


def fetch(checkout: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-S", str(checkout / "tools" / "fetch_models.py"), *arguments], capture_output=True, text=True, timeout=60)


def models(checkout: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted((checkout / "models").iterdir()) if path.name != "models.json"}


def test_it_downloads_each_reader_and_checks_it(checkout):
    done = fetch(checkout)
    assert done.returncode == 0, done.stderr
    assert models(checkout) == dict(sorted(FILES.items()))


def test_the_app_needs_only_the_onnx(checkout):
    assert fetch(checkout, "--onnx").returncode == 0
    assert sorted(models(checkout)) == ["chord_reader.onnx", "note_reader.onnx"]


def test_a_reader_already_there_is_not_downloaded_again(checkout):
    assert fetch(checkout).returncode == 0
    shutil.rmtree(checkout / "release")
    done = fetch(checkout)
    assert done.returncode == 0 and models(checkout) == dict(sorted(FILES.items()))


def test_a_download_that_is_not_the_file_listed_is_not_kept(checkout):
    (checkout / "release" / "note_reader.onnx").write_bytes(b"something else")
    done = fetch(checkout, "--onnx")
    assert done.returncode == 1 and "note_reader.onnx: the download isn't the file listed" in done.stderr
    assert sorted(models(checkout)) == ["chord_reader.onnx"], "nothing half-made or wrong is left behind"


def test_a_reader_that_differs_such_as_one_trained_here_is_kept_unless_forced(checkout):
    (checkout / "models" / "chord_reader.pt").write_bytes(b"trained here")
    done = fetch(checkout)
    assert done.returncode == 1 and "chord_reader.pt: differs from models-test" in done.stderr and "--force" in done.stderr
    assert models(checkout)["chord_reader.pt"] == b"trained here"
    assert fetch(checkout, "--force").returncode == 0
    assert models(checkout)["chord_reader.pt"] == FILES["chord_reader.pt"]


def test_check_downloads_nothing(checkout):
    done = fetch(checkout, "--check")
    assert done.returncode == 1 and "chord_reader.onnx: missing" in done.stderr and models(checkout) == {}
    fetch(checkout)
    assert fetch(checkout, "--check").returncode == 0


def test_the_listing_names_the_readers_the_code_loads_in_its_release():
    listing = json.loads((ROOT / "models" / "models.json").read_text(encoding="utf-8"))
    assert listing["url"] == f"https://github.com/Xernnn/xrnsheet/releases/download/{listing['release']}/"
    assert sorted(listing["files"]) == ["chord_reader.onnx", "chord_reader.pt", "note_reader.onnx", "note_reader.pt"]
    for entry in listing["files"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) and entry["bytes"] > 1_000_000
