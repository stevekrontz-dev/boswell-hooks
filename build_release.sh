#!/usr/bin/env bash
# Build the Claude Code release zip.
#
# v2.1.0 was assembled by hand and shipped without protected_paths.py, because
# nothing recorded what belonged in it. This script IS that record.
#
#   ./build_release.sh            -> dist/boswell-hooks.zip
#
# Layout note: the installed plugin is FLAT — .claude-plugin/, hooks/ and
# scripts/ all sit at the same level under ~/.claude/skills/boswell-hooks. So
# the zip takes the plugin manifest and hooks.json from claude/ (the Claude
# runtime root) but the REAL dispatcher from scripts/, not claude/scripts/,
# which is a 10-line shim that resolves parents[2] back to a repo checkout that
# will not exist on the target machine.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"
STAGE="$(mktemp -d)/boswell-hooks"
OUT="$ROOT/dist"

mkdir -p "$STAGE/scripts" "$STAGE/hooks" "$STAGE/.claude-plugin" "$STAGE/tests" "$OUT"

cp claude/.claude-plugin/plugin.json "$STAGE/.claude-plugin/"
cp claude/hooks/hooks.json           "$STAGE/hooks/"
cp INSTALL.md HANDOFF-FOR-CLAUDE.md LICENSE "$STAGE/"

# Handlers. Excludes in-tree test_*.py and the Windows-only tenant switcher.
for f in scripts/*.py; do
  case "$(basename "$f")" in
    test_*) continue ;;
  esac
  cp "$f" "$STAGE/scripts/"
done

# Only the test files that run standalone, without pytest — an installer
# verifying their own machine will not have it.
#
# DETECTED, not listed. The first version of this script named the two files
# explicitly and promptly dropped tests/test_deploy_memory.py from the v2.1.2
# build — the same hand-maintained-list failure that lost protected_paths.py
# from v2.1.0 and prompted writing this script at all. A test ships if it can
# be run directly; that property is visible in the file.
# PROVEN, not guessed. Detecting a __main__ guard was still too loose: it
# shipped tests/test_codex_hooks.py, which passes in the repo and FAILS in the
# flat install layout because the Codex files it reads are not in the zip. An
# installer running the handoff's verification steps would have seen a red
# failure on a correct install.
#
# So each candidate is copied into the staged tree and actually RUN there. It
# ships if it passes in the layout the installer will have. Exclusions are
# printed, never silent.
for f in tests/test_*.py; do
  grep -q '^if __name__ == "__main__":' "$f" || continue
  base=$(basename "$f")
  cp "$f" "$STAGE/tests/"
  if ( cd "$STAGE" && python "tests/$base" >/dev/null 2>&1 ); then
    echo "  ship  tests/$base"
  else
    rm -f "$STAGE/tests/$base"
    echo "  SKIP  tests/$base (does not pass in the flat install layout)"
  fi
done

# Running the candidate tests above executes code inside the staged tree, which
# leaves __pycache__ behind. Without this the artifact ships this machine's
# compiled bytecode — 14 stray entries in the first build that did it.
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.py[co]' -delete 2>/dev/null || true

# Normalise to LF. The source lives on Windows checkouts; the target is a Mac.
find "$STAGE" -type f \( -name '*.py' -o -name '*.json' -o -name '*.md' \) \
  -exec sed -i 's/\r$//' {} +

# Refuse to ship a key. This has never fired; it is here so it can.
if grep -rIqE 'bos_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY' "$STAGE"; then
  echo "REFUSING TO BUILD: credential-shaped string found in the staged tree" >&2
  exit 1
fi

VERSION=$(python -c "import json,sys; print(json.load(open('claude/.claude-plugin/plugin.json'))['version'])")

rm -f "$OUT/boswell-hooks.zip"
# python -m zipfile rather than zip(1): Git Bash on the Windows boxes has no
# zip binary, and the build has to run wherever the release is cut.
( cd "$(dirname "$STAGE")" && python -m zipfile -c "$OUT/boswell-hooks.zip" boswell-hooks )

echo "built v$VERSION -> $OUT/boswell-hooks.zip"
python - "$OUT/boswell-hooks.zip" <<'EOF'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
print("%d entries" % len(names))
for n in sorted(names):
    print("  " + n)
EOF
