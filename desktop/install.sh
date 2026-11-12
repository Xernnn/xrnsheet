#!/usr/bin/env bash
# Build xrnsheet's desktop app and (re)install it for this user. Run it again to update.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

APP_ID="xrnsheet"
APPS_DIR="$HOME/Applications"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons"
MIME_DIR="$HOME/.local/share/mime"
TARGET="$APPS_DIR/${APP_ID}.AppImage"

[[ -d node_modules ]] || npm ci
npm run pack

SOURCE="$(find dist -maxdepth 1 -name '*.AppImage' -type f -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
if [[ -z "${SOURCE:-}" ]]; then
  echo "No .AppImage in dist/." >&2
  exit 1
fi

mkdir -p "$APPS_DIR" "$DESKTOP_DIR" "$ICON_DIR" "$MIME_DIR/packages"

# Written beside the target and renamed over it: a running AppImage is read from this exact file,
# and truncating it would break the app in use. A rename leaves that copy running.
TEMP="$(mktemp "${TARGET}.new.XXXXXX")"
install -m 755 "$SOURCE" "$TEMP"
mv -f "$TEMP" "$TARGET"
install -m 644 build/icon.png "$ICON_DIR/${APP_ID}.png"

# MusicXML has no type of its own in the system's list, so .musicxml and .mxl files get one here,
# and "Open with" can offer xrnsheet for them. Plain .xml is left alone.
cat > "$MIME_DIR/packages/${APP_ID}-musicxml.xml" <<'MIME'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/vnd.recordare.musicxml+xml">
    <comment>MusicXML score</comment>
    <sub-class-of type="application/xml"/>
    <glob pattern="*.musicxml"/>
  </mime-type>
  <mime-type type="application/vnd.recordare.musicxml">
    <comment>Compressed MusicXML score</comment>
    <sub-class-of type="application/zip"/>
    <glob pattern="*.mxl"/>
  </mime-type>
</mime-info>
MIME
if command -v update-mime-database >/dev/null 2>&1; then
  update-mime-database "$MIME_DIR" >/dev/null 2>&1 || true
fi

# Run unpacked (NO_CLEANUP keeps the unpacked copy for the next start), as xrncal is: the server
# reads its code from the app all the time it runs, and an AppImage mounted in place loses those
# files if its mount goes away.
cat > "$DESKTOP_DIR/${APP_ID}.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=xrnsheet
Comment=Turn a photo or PDF of sheet music into notation you can edit and reprint
Exec=env NO_CLEANUP=1 "${TARGET}" --appimage-extract-and-run %F
Icon=${ICON_DIR}/${APP_ID}.png
Terminal=false
Categories=AudioVideo;Audio;Music;
MimeType=application/vnd.recordare.musicxml+xml;application/vnd.recordare.musicxml;
StartupWMClass=xrnsheet
DESKTOP
chmod 644 "$DESKTOP_DIR/${APP_ID}.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

# Each version unpacks to its own folder in the temporary folder, which is memory here, and stays
# there until a restart (about 300 MB each). Take away earlier versions' copies that nothing is
# running from; the next start unpacks again.
in_use() {
  local folder="$1" process link
  for process in /proc/[0-9]*; do
    for link in exe cwd; do
      [[ "$(readlink "$process/$link" 2>/dev/null)" == "$folder"/* || "$(readlink "$process/$link" 2>/dev/null)" == "$folder" ]] && return 0
    done
  done
  return 1
}
for folder in "${TMPDIR:-/tmp}"/appimage_extracted_*; do
  [[ -x "$folder/xrnsheet" && -f "$folder/resources/xrnsheet/app/server.py" ]] || continue
  in_use "$folder" || rm -rf -- "$folder"
done

echo "Installed: $TARGET"
echo "Scores are kept in ${XDG_DATA_HOME:-$HOME/.local/share}/xrnsheet/jobs"
