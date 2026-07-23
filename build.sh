#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="91"
OUTPUT_DIR="$PROJECT_DIR/dist"
STAGE_DIR="$PROJECT_DIR/build/dmg-stage"
TAURI_APP="$PROJECT_DIR/src-tauri/target/release/bundle/macos/$APP_NAME.app"
DMG_PATH="$OUTPUT_DIR/$APP_NAME.dmg"
ZIP_PATH="$OUTPUT_DIR/$APP_NAME.zip"

cd "$PROJECT_DIR"

if [[ ! -d node_modules ]]; then
    npm install
fi

rm -rf "$OUTPUT_DIR" "$STAGE_DIR"
mkdir -p "$OUTPUT_DIR" "$STAGE_DIR"

swift "$PROJECT_DIR/tools/generate_icon.swift" "$PROJECT_DIR/build/AppIcon.iconset"
python3 "$PROJECT_DIR/tools/pngs_to_icns.py" "$PROJECT_DIR/build/AppIcon.iconset" "$PROJECT_DIR/src-tauri/icons/icon.icns"
cp "$PROJECT_DIR/build/AppIcon.iconset/icon_512x512.png" "$PROJECT_DIR/src-tauri/icons/icon.png"

npm run tauri build -- --bundles app

if [[ ! -d "$TAURI_APP" ]]; then
    echo "Tauri app bundle was not created: $TAURI_APP" >&2
    exit 1
fi

codesign --force --deep --sign - "$TAURI_APP"
codesign --verify --deep --strict "$TAURI_APP"

ditto -c -k --sequesterRsrc --keepParent "$TAURI_APP" "$ZIP_PATH"

cp -R "$TAURI_APP" "$STAGE_DIR/$APP_NAME.app"
ln -s /Applications "$STAGE_DIR/Applications"
if ! hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$STAGE_DIR" \
    -ov \
    -format UDZO \
    "$DMG_PATH"; then
    rm -f "$DMG_PATH"
    echo "Warning: DMG creation failed; continuing with the ZIP package." >&2
fi

rm -rf "$TAURI_APP" "$STAGE_DIR"

[[ -f "$DMG_PATH" ]] && echo "$DMG_PATH"
echo "$ZIP_PATH"
