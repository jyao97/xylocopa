#!/usr/bin/env bash
# Bump version across package.json files, add a CHANGELOG stub, and create
# a git tag. Does NOT push — review the commit/tag and push manually.
#
# Why this script: the PWA manifest reads frontend/package.json at build
# time and pins icon URLs to https://cdn.jsdelivr.net/gh/...@v<version>/...
# so the GitHub tag MUST exist before the build is shipped to users.
# See vite.config.js ICON_BASE for context.
#
# Usage:
#   tools/bump-version.sh 0.10.2
#
# What it does:
#   1. Updates package.json (root) + frontend/package.json + frontend/package-lock.json
#   2. Inserts a new "## [<version>] - <today>" section in CHANGELOG.md (empty stub)
#   3. Commits "[release] v<version>"
#   4. Tags v<version> (annotated)
#
# After running:
#   - Edit CHANGELOG.md to fill in the new section
#   - git commit --amend (if you edited CHANGELOG before tagging) OR
#     append a follow-up commit then move the tag
#   - git push && git push --tags
#   - gh release create v<version> --notes "..."
#   - cd frontend && npx vite build && pm2 reload xylocopa-frontend

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <new-version>   e.g. $0 0.10.2" >&2
  exit 1
fi

NEW="$1"
if ! [[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "error: version must be x.y.z (got: $NEW)" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "error: working tree has uncommitted changes — commit or stash first" >&2
  git status --short >&2
  exit 1
fi

CUR=$(node -p "require('./frontend/package.json').version")
echo "→ bumping ${CUR} → ${NEW}"

# 1. update package.json files (npm version --no-git-tag-version handles json + lockfile)
( cd "$ROOT" && npm version "$NEW" --no-git-tag-version --allow-same-version >/dev/null )
( cd "$ROOT/frontend" && npm version "$NEW" --no-git-tag-version --allow-same-version >/dev/null )

# 2. insert empty stub into CHANGELOG.md just below "## [Unreleased]"
TODAY=$(date -u +%Y-%m-%d)
python3 - "$NEW" "$TODAY" <<'PY'
import re, sys, pathlib
new, today = sys.argv[1], sys.argv[2]
p = pathlib.Path("CHANGELOG.md")
text = p.read_text()
stub = f"""## [Unreleased]

## [{new}] - {today}

### Added

-

### Fixed

-

### Changed

-

"""
new_text, n = re.subn(r"## \[Unreleased\]\n+", stub, text, count=1)
if n != 1:
    print("error: could not find '## [Unreleased]' anchor in CHANGELOG.md", file=sys.stderr)
    sys.exit(1)
p.write_text(new_text)
PY

# 3. commit (only stage files that exist — root package-lock.json may be absent)
STAGED=(package.json frontend/package.json frontend/package-lock.json CHANGELOG.md)
[ -f package-lock.json ] && STAGED+=(package-lock.json)
git add "${STAGED[@]}"
git commit -m "[release] v${NEW}"

# 4. tag (annotated)
git tag -a "v${NEW}" -m "v${NEW}"

cat <<EOF

✓ bumped to ${NEW}, committed, tagged v${NEW}

Next steps:
  1. Edit CHANGELOG.md to fill in the new ${NEW} section
  2. git commit --amend --no-edit  (folds CHANGELOG edits into the release commit)
     git tag -f v${NEW}             (move tag to amended commit)
  3. git push origin master
     git push origin v${NEW}        (jsDelivr CDN won't serve icons until tag is pushed!)
  4. gh release create v${NEW} --notes "..."
  5. cd frontend && npx vite build && pm2 reload xylocopa-frontend

EOF
