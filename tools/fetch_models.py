#!/usr/bin/env python3
"""Download the trained readers into models/ and check each against its SHA-256.

They're kept as files of a GitHub release, not in git; models/models.json names the release, the
files and their hashes.

    python3 tools/fetch_models.py           all four: the .onnx the app reads and the .pt training writes
    python3 tools/fetch_models.py --onnx    only the .onnx, which is all the app needs to read a sheet
    python3 tools/fetch_models.py --check   download nothing, only check what's there

A file already there and right isn't downloaded again. One that differs, such as a reader trained
here, is left as it is unless --force is given. It ends with 1 unless every file asked for is there
and right. Only Python's own library is used, so it runs before anything is installed.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models"
LISTING = MODEL_DIR / "models.json"
CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, path: Path, sha256: str, size: int) -> str | None:
    """Download url to path, and keep it only if it's the file listed. Returns what went wrong, if anything."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    try:
        digest = hashlib.sha256()
        with os.fdopen(handle, "wb") as out, urllib.request.urlopen(url, timeout=60) as response:
            while block := response.read(CHUNK):
                digest.update(block)
                out.write(block)
        got = os.path.getsize(temporary)
        if got != size or digest.hexdigest() != sha256:
            return f"the download isn't the file listed ({got} bytes, SHA-256 {digest.hexdigest()[:12]}…)"
        os.replace(temporary, path)
        return None
    except OSError as error:  # urllib's errors are OSErrors too.
        return f"couldn't download it: {error}"
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onnx", action="store_true", help="only the .onnx files, which the app reads")
    parser.add_argument("--check", action="store_true", help="download nothing; check the files already there")
    parser.add_argument("--force", action="store_true", help="replace a file that differs from the release")
    parser.add_argument("--listing", type=Path, default=LISTING, help="the models.json to go by (default: models/models.json)")
    arguments = parser.parse_args(argv)

    listing = json.loads(arguments.listing.read_text(encoding="utf-8"))
    files = {name: entry for name, entry in listing["files"].items() if not arguments.onnx or name.endswith(".onnx")}
    MODEL_DIR.mkdir(exist_ok=True)
    wrong, kept = [], []
    for name, entry in files.items():
        path = MODEL_DIR / name
        if path.exists() and sha256_of(path) == entry["sha256"]:
            print(f"{name}: right")
        elif path.exists() and (arguments.check or not arguments.force):
            wrong.append(name)
            kept.append(name)
            print(f"{name}: differs from {listing['release']}", file=sys.stderr)
        elif arguments.check:
            wrong.append(name)
            print(f"{name}: missing", file=sys.stderr)
        else:
            print(f"{name}: downloading {entry['bytes'] / 1e6:.1f} MB from {listing['release']}", flush=True)
            problem = download(listing["url"] + name, path, entry["sha256"], entry["bytes"])
            if problem:
                wrong.append(name)
                print(f"{name}: {problem}", file=sys.stderr)
    if kept and not arguments.check:
        print(f"Left as it is: {', '.join(kept)}. --force replaces it with the release's.", file=sys.stderr)
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
