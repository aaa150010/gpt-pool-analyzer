#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="${1:-}"
TAG="v$VERSION"
REPOSITORY="aaa150010/gpt-pool-analyzer"
SPARKLE_ACCOUNT="gpt-pool-analyzer"
SPARKLE_VERSION="2.9.4"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Usage: ./release.sh <version>, for example: ./release.sh 1.0.1" >&2
    exit 1
fi

cd "$PROJECT_DIR"

if [[ -n "$(git status --porcelain -- . ':(exclude)RELEASE_NOTES.md')" ]]; then
    echo "Commit existing changes before creating a release. RELEASE_NOTES.md may remain uncommitted." >&2
    exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Tag $TAG already exists." >&2
    exit 1
fi

gh auth status >/dev/null

CURRENT_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' Info.plist)"
CURRENT_BUILD="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' Info.plist)"

if [[ "$VERSION" != "$CURRENT_VERSION" ]]; then
    NEXT_BUILD="$((CURRENT_BUILD + 1))"
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" Info.plist
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $NEXT_BUILD" Info.plist
fi

./build.sh

RELEASE_DIR="$PROJECT_DIR/build/release"
UPDATE_DIR="$RELEASE_DIR/updates"
SPARKLE_DIR="$PROJECT_DIR/.cache/sparkle/Sparkle-$SPARKLE_VERSION"
mkdir -p "$UPDATE_DIR"

cp "dist/GPT分析器.zip" "$UPDATE_DIR/GPTAnalyzer-$VERSION.zip"
cp RELEASE_NOTES.md "$UPDATE_DIR/GPTAnalyzer-$VERSION.md"

RELEASE_ASSETS=(
    "$RELEASE_DIR/GPTAnalyzer-$VERSION.zip"
    "$RELEASE_DIR/appcast.xml"
    "$RELEASE_DIR/SHA256SUMS.txt"
)

if [[ -f "dist/GPT分析器.dmg" ]]; then
    cp "dist/GPT分析器.dmg" "$RELEASE_DIR/GPTAnalyzer-$VERSION.dmg"
    RELEASE_ASSETS=("$RELEASE_DIR/GPTAnalyzer-$VERSION.dmg" "${RELEASE_ASSETS[@]}")
fi

"$SPARKLE_DIR/bin/generate_appcast" \
    --account "$SPARKLE_ACCOUNT" \
    --download-url-prefix "https://github.com/$REPOSITORY/releases/download/$TAG/" \
    --link "https://github.com/$REPOSITORY" \
    --embed-release-notes \
    --maximum-versions 1 \
    -o "$RELEASE_DIR/appcast.xml" \
    "$UPDATE_DIR"

mv "$UPDATE_DIR/GPTAnalyzer-$VERSION.zip" "$RELEASE_DIR/GPTAnalyzer-$VERSION.zip"
shasum -a 256 "$RELEASE_DIR/GPTAnalyzer-$VERSION.zip" > "$RELEASE_DIR/SHA256SUMS.txt"
if [[ -f "$RELEASE_DIR/GPTAnalyzer-$VERSION.dmg" ]]; then
    shasum -a 256 "$RELEASE_DIR/GPTAnalyzer-$VERSION.dmg" >> "$RELEASE_DIR/SHA256SUMS.txt"
fi

if [[ -n "$(git status --porcelain -- Info.plist RELEASE_NOTES.md)" ]]; then
    git add Info.plist RELEASE_NOTES.md
    git commit -m "Release $TAG"
fi

git tag -a "$TAG" -m "GPT分析器 $VERSION"
git push origin main
git push origin "$TAG"

gh release create "$TAG" \
    "${RELEASE_ASSETS[@]}" \
    --repo "$REPOSITORY" \
    --title "GPT分析器 $VERSION" \
    --notes-file RELEASE_NOTES.md

rm -rf "$PROJECT_DIR/build/GPT分析器.app" "$PROJECT_DIR/build/dmg-stage"

echo "Release $TAG is available at https://github.com/$REPOSITORY/releases/tag/$TAG"
