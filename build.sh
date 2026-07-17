#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$PROJECT_DIR/build"
OUTPUT_DIR="$PROJECT_DIR/dist"
APP_NAME="GPT分析器"
APP_DIR="$BUILD_DIR/$APP_NAME.app"
STAGE_DIR="$BUILD_DIR/dmg-stage"
DMG_PATH="$OUTPUT_DIR/$APP_NAME.dmg"
ZIP_PATH="$OUTPUT_DIR/$APP_NAME.zip"

SPARKLE_VERSION="2.9.4"
SPARKLE_SHA256="ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9"
SPARKLE_CACHE_DIR="${SPARKLE_CACHE_DIR:-$PROJECT_DIR/.cache/sparkle}"
SPARKLE_ARCHIVE="$SPARKLE_CACHE_DIR/Sparkle-$SPARKLE_VERSION.tar.xz"
SPARKLE_DIR="$SPARKLE_CACHE_DIR/Sparkle-$SPARKLE_VERSION"
SPARKLE_URL="https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz"

VERSION="${VERSION:-$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PROJECT_DIR/Info.plist")}"
BUILD_NUMBER="${BUILD_NUMBER:-$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$PROJECT_DIR/Info.plist")}"
MIN_MACOS_VERSION="12.0"

download_sparkle() {
    mkdir -p "$SPARKLE_CACHE_DIR"

    if [[ ! -f "$SPARKLE_ARCHIVE" ]]; then
        curl --fail --location "$SPARKLE_URL" --output "$SPARKLE_ARCHIVE"
    fi

    local actual_sha
    actual_sha="$(shasum -a 256 "$SPARKLE_ARCHIVE" | awk '{print $1}')"
    if [[ "$actual_sha" != "$SPARKLE_SHA256" ]]; then
        rm -f "$SPARKLE_ARCHIVE"
        echo "Sparkle archive checksum mismatch." >&2
        exit 1
    fi

    if [[ ! -d "$SPARKLE_DIR/Sparkle.framework" ]]; then
        rm -rf "$SPARKLE_DIR"
        mkdir -p "$SPARKLE_DIR"
        tar -xf "$SPARKLE_ARCHIVE" -C "$SPARKLE_DIR"
    fi
}

download_sparkle

rm -rf "$BUILD_DIR" "$OUTPUT_DIR"
mkdir -p \
    "$APP_DIR/Contents/MacOS" \
    "$APP_DIR/Contents/Resources" \
    "$APP_DIR/Contents/Frameworks" \
    "$STAGE_DIR" \
    "$OUTPUT_DIR"

export CLANG_MODULE_CACHE_PATH="$BUILD_DIR/module-cache"

swift "$PROJECT_DIR/tools/generate_icon.swift" "$BUILD_DIR/AppIcon.iconset"
if ! iconutil -c icns "$BUILD_DIR/AppIcon.iconset" -o "$APP_DIR/Contents/Resources/AppIcon.icns"; then
    if python3 "$PROJECT_DIR/tools/pngs_to_icns.py" "$BUILD_DIR/AppIcon.iconset" "$APP_DIR/Contents/Resources/AppIcon.icns"; then
        :
    elif [[ -f "/Applications/GPT分析器.app/Contents/Resources/AppIcon.icns" ]]; then
        cp "/Applications/GPT分析器.app/Contents/Resources/AppIcon.icns" "$APP_DIR/Contents/Resources/AppIcon.icns"
    else
        echo "Warning: iconutil failed and no fallback icon was available." >&2
    fi
fi

SDK_PATH="$(xcrun --sdk macosx --show-sdk-path)"
for arch in arm64 x86_64; do
    swiftc "$PROJECT_DIR/Sources/main.swift" \
        -O \
        -sdk "$SDK_PATH" \
        -target "$arch-apple-macosx$MIN_MACOS_VERSION" \
        -framework Cocoa \
        -framework Vision \
        -F "$SPARKLE_DIR" \
        -framework Sparkle \
        -Xlinker -rpath \
        -Xlinker "@executable_path/../Frameworks" \
        -o "$BUILD_DIR/$APP_NAME-$arch"
done

lipo -create \
    "$BUILD_DIR/$APP_NAME-arm64" \
    "$BUILD_DIR/$APP_NAME-x86_64" \
    -output "$APP_DIR/Contents/MacOS/$APP_NAME"

ditto "$SPARKLE_DIR/Sparkle.framework" "$APP_DIR/Contents/Frameworks/Sparkle.framework"
cp "$PROJECT_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD_NUMBER" "$APP_DIR/Contents/Info.plist"

codesign --force --deep --sign - "$APP_DIR"
codesign --verify --deep --strict "$APP_DIR"

cp -R "$APP_DIR" "$STAGE_DIR/$APP_NAME.app"
ln -s /Applications "$STAGE_DIR/Applications"

hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$STAGE_DIR" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

ditto -c -k --sequesterRsrc --keepParent "$APP_DIR" "$ZIP_PATH"

echo "$DMG_PATH"
echo "$ZIP_PATH"
