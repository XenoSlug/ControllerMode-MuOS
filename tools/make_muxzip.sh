#!/bin/bash
set -euo pipefail

# tools/make_muxzip.sh
# Packages ControllerMode-MuOS into a .muxzip archive for muOS

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
TEMP_DIR="$ROOT_DIR/build/temp_pkg"
PKG_NAME="ControllerMode-MuOS.muxzip"

echo "=== Building $PKG_NAME for muOS ==="

rm -rf "$TEMP_DIR" "$DIST_DIR"
mkdir -p "$DIST_DIR"
mkdir -p "$TEMP_DIR/MUOS/application/ControllerMode"

APP_DEST="$TEMP_DIR/MUOS/application/ControllerMode"

# Copy application files
cp "$ROOT_DIR/mux_launch.sh" "$APP_DEST/"
chmod +x "$APP_DEST/mux_launch.sh"

cp "$ROOT_DIR/icon.png" "$APP_DEST/"
cp "$ROOT_DIR/README.md" "$APP_DEST/" 2>/dev/null || true

cp -r "$ROOT_DIR/app" "$APP_DEST/"
cp -r "$ROOT_DIR/assets" "$APP_DEST/"

# Remove bytecode cache
find "$APP_DEST" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$APP_DEST" -type f -name "*.pyc" -delete 2>/dev/null || true

# Package into zip with .muxzip extension
cd "$TEMP_DIR"
zip -r "$DIST_DIR/$PKG_NAME" MUOS/

echo "=== Packaging Complete ==="
echo "Output: $DIST_DIR/$PKG_NAME"
ls -lh "$DIST_DIR/$PKG_NAME"
