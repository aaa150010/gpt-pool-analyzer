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

if ! gh auth status >/dev/null; then
    echo "Warning: gh auth status failed; continuing and letting git/gh release commands report any real auth errors." >&2
fi

CURRENT_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' Info.plist)"
CURRENT_BUILD="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' Info.plist)"

if [[ "$VERSION" != "$CURRENT_VERSION" ]]; then
    NEXT_BUILD="$((CURRENT_BUILD + 1))"
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" Info.plist
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $NEXT_BUILD" Info.plist
fi

node -e '
const fs = require("fs");
const version = process.argv[1];
for (const file of ["package.json", "package-lock.json"]) {
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  data.version = version;
  if (data.packages && data.packages[""]) data.packages[""].version = version;
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n");
}
const tauriFile = "src-tauri/tauri.conf.json";
const tauri = JSON.parse(fs.readFileSync(tauriFile, "utf8"));
tauri.version = version;
fs.writeFileSync(tauriFile, JSON.stringify(tauri, null, 2) + "\n");
' "$VERSION"

perl -0pi -e "s/^version = \"[^\"]+\"/version = \"$VERSION\"/m" src-tauri/Cargo.toml
perl -0pi -e "s/(name = \"gpt-analyzer\"\\nversion = \")[^\"]+\"/\${1}$VERSION\"/" src-tauri/Cargo.lock

./build.sh

RELEASE_DIR="$PROJECT_DIR/build/release"
UPDATE_DIR="$RELEASE_DIR/updates"
SPARKLE_DIR="$PROJECT_DIR/.cache/sparkle/Sparkle-$SPARKLE_VERSION"
rm -rf "$RELEASE_DIR"
mkdir -p "$UPDATE_DIR"

cp "dist/91.zip" "$UPDATE_DIR/91-$VERSION.zip"
cp RELEASE_NOTES.md "$UPDATE_DIR/91-$VERSION.md"

RELEASE_ASSETS=(
    "$RELEASE_DIR/91-$VERSION.zip"
    "$RELEASE_DIR/appcast.xml"
    "$RELEASE_DIR/SHA256SUMS.txt"
)

if [[ -f "dist/91.dmg" ]]; then
    cp "dist/91.dmg" "$RELEASE_DIR/91-$VERSION.dmg"
    RELEASE_ASSETS=("$RELEASE_DIR/91-$VERSION.dmg" "${RELEASE_ASSETS[@]}")
fi

"$SPARKLE_DIR/bin/generate_appcast" \
    --account "$SPARKLE_ACCOUNT" \
    --download-url-prefix "https://github.com/$REPOSITORY/releases/download/$TAG/" \
    --link "https://github.com/$REPOSITORY" \
    --embed-release-notes \
    --maximum-versions 1 \
    -o "$RELEASE_DIR/appcast.xml" \
    "$UPDATE_DIR"

mv "$UPDATE_DIR/91-$VERSION.zip" "$RELEASE_DIR/91-$VERSION.zip"
shasum -a 256 "$RELEASE_DIR/91-$VERSION.zip" > "$RELEASE_DIR/SHA256SUMS.txt"
if [[ -f "$RELEASE_DIR/91-$VERSION.dmg" ]]; then
    shasum -a 256 "$RELEASE_DIR/91-$VERSION.dmg" >> "$RELEASE_DIR/SHA256SUMS.txt"
fi

if [[ -n "$(git status --porcelain -- Info.plist RELEASE_NOTES.md package.json package-lock.json src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock)" ]]; then
    git add Info.plist RELEASE_NOTES.md package.json package-lock.json src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock
    git commit -m "Release $TAG"
fi

git tag -a "$TAG" -m "91 $VERSION"
git push origin main
git push origin "$TAG"

gh release create "$TAG" \
    "${RELEASE_ASSETS[@]}" \
    --repo "$REPOSITORY" \
    --title "91 $VERSION" \
    --notes-file RELEASE_NOTES.md

rm -rf "$PROJECT_DIR/build/91.app" "$PROJECT_DIR/build/dmg-stage"

echo "Release $TAG is available at https://github.com/$REPOSITORY/releases/tag/$TAG"
