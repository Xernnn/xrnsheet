#!/usr/bin/env bash
# Gather what goes inside the desktop app: the server's committed code, the readers models/models.json
# names, the Python it runs on, and the icon. `npm run pack` runs this before electron-builder.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
REPO="$(cd .. && pwd)"
STAGE=build/xrnsheet
PARTS=(app chords notes pipeline)

# The Python environment with xrnsheet's packages (about 1.8 GB, with PyTorch's CPU build), used
# where it is rather than copied into the app. The checkout's .venv by default; a worktree's .venv
# may be a link to it.
if [[ -n "${XRNSHEET_PYTHON:-}" ]]; then
  PYTHON="$XRNSHEET_PYTHON"
else
  [[ -e "$REPO/.venv" ]] || { echo "No .venv in $REPO. Set it up as the README's Setup says, or set XRNSHEET_PYTHON." >&2; exit 1; }
  PYTHON="$(realpath "$REPO/.venv")/bin/python"
fi
[[ -x "$PYTHON" ]] || { echo "$PYTHON isn't a Python that can be run." >&2; exit 1; }

# Only what's committed, so a half-made change in the checkout can't slip into the installed app.
rm -rf "$STAGE"
mkdir -p "$STAGE"
git -C "$REPO" archive --format=tar HEAD -- "${PARTS[@]}" models/models.json | tar -x -C "$STAGE"
if [[ -n "$(git -C "$REPO" status --porcelain -- "${PARTS[@]}" models/models.json)" ]]; then
  echo "Note: changes not yet committed in ${PARTS[*]} models/models.json are left out of the app." >&2
fi

# The readers aren't in git: they're files of a release, fetched into models/. Only the ones the
# committed models.json lists go in, each checked against its SHA-256, so a reader trained here and
# not yet released can't slip in either.
"$PYTHON" "$REPO/tools/fetch_models.py" --check --listing "$STAGE/models/models.json" >/dev/null \
  || { echo "The readers in $REPO/models aren't the ones models/models.json lists. Fetch them with: python3 tools/fetch_models.py" >&2; exit 1; }
"$PYTHON" -c 'import json, sys; print("\n".join(json.load(open(sys.argv[1]))["files"]))' "$STAGE/models/models.json" \
  | while read -r name; do cp "$REPO/models/$name" "$STAGE/models/$name"; done
COMMIT="$(git -C "$REPO" rev-parse --short HEAD)"

# Check that this Python can run the server the app carries, before building around it.
(cd "$STAGE" && "$PYTHON" -c "import app.server") || { echo "$PYTHON can't run xrnsheet's server. Install requirements.txt into it." >&2; exit 1; }
"$PYTHON" -m compileall -q "$STAGE"

# The server runs from the app's copy, so it's told where this checkout gathers corrections for retraining.
printf '{\n  "python": "%s",\n  "corrections": "%s",\n  "commit": "%s"\n}\n' "$PYTHON" "$REPO/data/corrections" "$COMMIT" > build/desktop.json
rsvg-convert -w 512 -h 512 "$REPO/app/static/icon.svg" -o build/icon.png
echo "Staged xrnsheet $COMMIT, running on $PYTHON"
