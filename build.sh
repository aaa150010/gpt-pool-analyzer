#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="91"
OUTPUT_DIR="$PROJECT_DIR/dist"
STAGE_DIR="$PROJECT_DIR/build/dmg-stage"
TAURI_APP="$PROJECT_DIR/src-tauri/target/release/bundle/macos/$APP_NAME.app"
UPDATER_ARCHIVE="$PROJECT_DIR/src-tauri/target/release/bundle/macos/$APP_NAME.app.tar.gz"
UPDATER_SIGNATURE="$UPDATER_ARCHIVE.sig"
DMG_PATH="$OUTPUT_DIR/$APP_NAME.dmg"
ZIP_PATH="$OUTPUT_DIR/$APP_NAME.zip"
UPDATER_ARCHIVE_PATH="$OUTPUT_DIR/$APP_NAME.app.tar.gz"
UPDATER_SIGNATURE_PATH="$UPDATER_ARCHIVE_PATH.sig"

cd "$PROJECT_DIR"

if [[ ! -d node_modules ]]; then
    npm install
fi

rm -rf "$OUTPUT_DIR" "$STAGE_DIR"
mkdir -p "$OUTPUT_DIR" "$STAGE_DIR"

if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" && -z "${TAURI_SIGNING_PRIVATE_KEY_PATH:-}" ]]; then
    DEFAULT_SIGNING_KEY="$HOME/.tauri/91-updater.key"
    if [[ ! -f "$DEFAULT_SIGNING_KEY" ]]; then
        echo "Updater signing key is missing: $DEFAULT_SIGNING_KEY" >&2
        echo "Generate it with: npm run tauri signer generate -- --ci --password '<password>' -w '$DEFAULT_SIGNING_KEY'" >&2
        exit 1
    fi
    export TAURI_SIGNING_PRIVATE_KEY_PATH="$DEFAULT_SIGNING_KEY"
fi

if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" && -n "${TAURI_SIGNING_PRIVATE_KEY_PATH:-}" ]]; then
    export TAURI_SIGNING_PRIVATE_KEY="$(<"$TAURI_SIGNING_PRIVATE_KEY_PATH")"
fi

if [[ -z "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}" && -n "${TAURI_SIGNING_PRIVATE_KEY_PATH:-}" ]]; then
    DEFAULT_SIGNING_PASSWORD_FILE="$TAURI_SIGNING_PRIVATE_KEY_PATH.pass"
    if [[ -f "$DEFAULT_SIGNING_PASSWORD_FILE" ]]; then
        export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(<"$DEFAULT_SIGNING_PASSWORD_FILE")"
    fi
fi

if swift "$PROJECT_DIR/tools/generate_icon.swift" "$PROJECT_DIR/build/AppIcon.iconset"; then
    python3 "$PROJECT_DIR/tools/pngs_to_icns.py" "$PROJECT_DIR/build/AppIcon.iconset" "$PROJECT_DIR/src-tauri/icons/icon.icns"
    cp "$PROJECT_DIR/build/AppIcon.iconset/icon_512x512.png" "$PROJECT_DIR/src-tauri/icons/icon.png"
else
    echo "Warning: Swift icon generation failed; reusing the checked-in 91 icons." >&2
    if [[ ! -f "$PROJECT_DIR/src-tauri/icons/icon.icns" || ! -f "$PROJECT_DIR/src-tauri/icons/icon.png" ]]; then
        echo "Checked-in icons are missing." >&2
        exit 1
    fi
fi

npm run tauri build -- --bundles app

if [[ ! -d "$TAURI_APP" ]]; then
    echo "Tauri app bundle was not created: $TAURI_APP" >&2
    exit 1
fi

codesign --force --deep --sign - "$TAURI_APP"
codesign --verify --deep --strict "$TAURI_APP"

ditto -c -k --sequesterRsrc --keepParent "$TAURI_APP" "$ZIP_PATH"
if [[ -f "$UPDATER_ARCHIVE" && -f "$UPDATER_SIGNATURE" ]]; then
    cp "$UPDATER_ARCHIVE" "$UPDATER_ARCHIVE_PATH"
    cp "$UPDATER_SIGNATURE" "$UPDATER_SIGNATURE_PATH"
else
    echo "Updater artifact was not created: $UPDATER_ARCHIVE" >&2
    exit 1
fi

# Tauri's build cleanup may remove the project build directory.
mkdir -p "$STAGE_DIR"
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
echo "$UPDATER_ARCHIVE_PATH"
