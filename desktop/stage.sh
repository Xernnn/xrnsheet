#!/usr/bin/env bash
# Gather what goes inside the desktop app: the server's committed code and models, the Python it
# runs on, and the icon. `npm run pack` runs this before electron-builder.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
REPO="$(cd .. && pwd)"
STAGE=build/xrnsheet
PARTS=(app chords notes pipeline models)

# The Python environment with xrnsheet's packages (6 GB with PyTorch), used where it is rather than
# copied into the app. The checkout's .venv by default; a worktree's .venv may be a link to it.
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
git -C "$REPO" archive --format=tar HEAD -- "${PARTS[@]}" | tar -x -C "$STAGE"
if [[ -n "$(git -C "$REPO" status --porcelain -- "${PARTS[@]}")" ]]; then
  echo "Note: changes not yet committed in ${PARTS[*]} are left out of the app." >&2
fi
COMMIT="$(git -C "$REPO" rev-parse --short HEAD)"

# Check that this Python can run the server the app carries, before building around it.
(cd "$STAGE" && "$PYTHON" -c "import app.server") || { echo "$PYTHON can't run xrnsheet's server. Install requirements.txt into it." >&2; exit 1; }
"$PYTHON" -m compileall -q "$STAGE"

printf '{\n  "python": "%s",\n  "commit": "%s"\n}\n' "$PYTHON" "$COMMIT" > build/desktop.json
rsvg-convert -w 512 -h 512 "$REPO/app/static/icon.svg" -o build/icon.png
echo "Staged xrnsheet $COMMIT, running on $PYTHON"
