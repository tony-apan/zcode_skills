#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ "${1-}" = "--help" ] || [ "${1-}" = "-h" ]; then
  printf '%s\n' "Usage: scripts/release.sh"
  printf '%s\n' "Validate a clean release and create a local annotated vVERSION tag."
  printf '%s\n' 'After review, run: git push origin main "v$VERSION"'
  exit 0
fi
if [ "$#" -ne 0 ]; then
  printf '%s\n' "Usage: scripts/release.sh" >&2
  exit 2
fi

VERSION=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "$ROOT/.zcode-plugin/plugin.json")
TAG="v$VERSION"

python3 "$SCRIPT_DIR/manage.py" validate
(
  cd "$ROOT"
  python3 -m unittest discover -s tests -p 'test_*.py' -v
)
grep -Eq "^## \[$VERSION\]" "$ROOT/CHANGELOG.md" || {
  printf '%s\n' "CHANGELOG.md does not contain $VERSION" >&2
  exit 1
}
git -C "$ROOT" diff --quiet
git -C "$ROOT" diff --cached --quiet
test -z "$(git -C "$ROOT" status --porcelain)" || {
  printf '%s\n' "Working tree is not clean" >&2
  exit 1
}
if git -C "$ROOT" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  printf '%s\n' "Tag already exists: $TAG" >&2
  exit 1
fi

git -C "$ROOT" tag -a "$TAG" -m "tony-agents-pack $TAG"
printf '%s\n' "Created local tag $TAG"
printf '%s\n' "Next: git push origin main \"$TAG\""
