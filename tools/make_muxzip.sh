#!/bin/bash
set -euo pipefail

# tools/make_muxzip.sh
# Packages ControllerMode-MuOS for muOS Archive Manager

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
TEMP_DIR="$ROOT_DIR/build/temp_pkg"
PKG_MUXZIP="ControllerMode-MuOS.muxzip"
PKG_MUXAPP="ControllerMode-MuOS.muxapp"

echo "=== Building Packages for muOS ==="

rm -rf "$TEMP_DIR" "$DIST_DIR"
mkdir -p "$DIST_DIR"

# 1. Structure for .muxzip:
# muOS extract.sh expects top-level section name matching /opt/muos/script/archive/$TOP.sh -> 'application'
APP_DEST="$TEMP_DIR/application/ControllerMode"
mkdir -p "$APP_DEST"

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

# Build .muxzip (contains application/ControllerMode/...)
cd "$TEMP_DIR"
zip -rq "$DIST_DIR/$PKG_MUXZIP" application/
echo "Created: $DIST_DIR/$PKG_MUXZIP"

# 2. Build .muxapp (contains ControllerMode/... at root)
cd "$TEMP_DIR/application"
zip -rq "$DIST_DIR/$PKG_MUXAPP" ControllerMode/
echo "Created: $DIST_DIR/$PKG_MUXAPP"

echo "=== Packaging Complete ==="
ls -lh "$DIST_DIR"
