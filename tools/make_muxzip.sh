#!/bin/bash
set -euo pipefail

# tools/make_muxzip.sh
# Packages ControllerMode-MuOS into ControllerMode-MuOS.muxapp for muOS Archive Manager

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
STAGE_DIR="$ROOT_DIR/build/stage"
PKG_NAME="ControllerMode-MuOS.muxapp"
APP_NAME="ControllerMode"

echo "=== Building $PKG_NAME for muOS ==="

rm -rf "$STAGE_DIR" "$DIST_DIR"
mkdir -p "$DIST_DIR"
mkdir -p "$STAGE_DIR/$APP_NAME"

APP_DEST="$STAGE_DIR/$APP_NAME"

cp "$ROOT_DIR/mux_launch.sh" "$APP_DEST/"
chmod +x "$APP_DEST/mux_launch.sh"

cp "$ROOT_DIR/icon.png" "$APP_DEST/"
cp -r "$ROOT_DIR/glyph" "$APP_DEST/"
cp -r "$ROOT_DIR/app" "$APP_DEST/"
cp -r "$ROOT_DIR/assets" "$APP_DEST/"
cp "$ROOT_DIR/README.md" "$APP_DEST/" 2>/dev/null || true

# Remove bytecode and macOS metadata
find "$APP_DEST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$APP_DEST" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$APP_DEST" -name ".DS_Store" -delete 2>/dev/null || true

# Package as .muxapp (archive contains ControllerMode/ at root)
cd "$STAGE_DIR"
zip -rq "$DIST_DIR/$PKG_NAME" "$APP_NAME"

echo "=== Packaging Complete ==="
ls -lh "$DIST_DIR/$PKG_NAME"
