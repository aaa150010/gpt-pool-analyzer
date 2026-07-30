#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="${1:-}"
TAG="v$VERSION"
REPOSITORY="aaa150010/gpt-pool-analyzer"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Usage: ./release.sh <version>, for example: ./release.sh 1.0.1" >&2
    exit 1
fi

cd "$PROJECT_DIR"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Commit existing changes before creating a release." >&2
    exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "Tag $TAG already exists." >&2
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "GitHub CLI authentication is required before release. Run: gh auth login -h github.com" >&2
    exit 1
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
rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

cp "dist/GPT分析器.zip" "$RELEASE_DIR/GPT分析器-$VERSION.zip"

RELEASE_ASSETS=(
    "$RELEASE_DIR/GPT分析器-$VERSION.zip"
    "$RELEASE_DIR/SHA256SUMS.txt"
)

if [[ -f "dist/GPT分析器.dmg" ]]; then
    cp "dist/GPT分析器.dmg" "$RELEASE_DIR/GPT分析器-$VERSION.dmg"
    RELEASE_ASSETS=("$RELEASE_DIR/GPT分析器-$VERSION.dmg" "${RELEASE_ASSETS[@]}")
fi

shasum -a 256 "$RELEASE_DIR/GPT分析器-$VERSION.zip" > "$RELEASE_DIR/SHA256SUMS.txt"
if [[ -f "$RELEASE_DIR/GPT分析器-$VERSION.dmg" ]]; then
    shasum -a 256 "$RELEASE_DIR/GPT分析器-$VERSION.dmg" >> "$RELEASE_DIR/SHA256SUMS.txt"
fi

if [[ -n "$(git status --porcelain -- Info.plist RELEASE_NOTES.md package.json package-lock.json src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock)" ]]; then
    git add Info.plist RELEASE_NOTES.md package.json package-lock.json src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock
    git commit -m "Release $TAG"
fi

git push origin main

gh release create "$TAG" \
    "${RELEASE_ASSETS[@]}" \
    --repo "$REPOSITORY" \
    --target "$(git rev-parse HEAD)" \
    --draft \
    --title "GPT分析器 $VERSION" \
    --notes-file RELEASE_NOTES.md

gh release edit "$TAG" --repo "$REPOSITORY" --draft=false
git fetch origin "refs/tags/$TAG:refs/tags/$TAG"

rm -rf "$PROJECT_DIR/build/GPT分析器.app" "$PROJECT_DIR/build/dmg-stage"

echo "Release $TAG is available at https://github.com/$REPOSITORY/releases/tag/$TAG"
