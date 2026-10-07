#!/usr/bin/env bash
# Create the launch labels and good-first-issues on GitHub. For the maintainer, after review.
#
#   DRY_RUN=1 (default)  print what would be done; change nothing on GitHub
#   DRY_RUN=0            create the labels and the issues
#
# Usage (from the repository root):
#   bash docs/launch/create-issues.sh                 # dry run
#   DRY_RUN=0 bash docs/launch/create-issues.sh       # for real
#
# Idempotent: labels are created with --force (create or update), and an issue is skipped when an
# issue (open or closed) with exactly the same title already exists.
#
# Needs: gh (authenticated: gh auth status), and Python with PyYAML (default: "uv run python";
# override with PYTHON="python3"). Nothing here touches Discussions: see docs/launch/discussions.md.

set -euo pipefail

DRY_RUN="${DRY_RUN:-1}"
REPO="${REPO:-anilatambharii/tycheon}"
PYTHON="${PYTHON:-uv run python}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISSUES_FILE="${ISSUES_FILE:-$HERE/good-first-issues.yaml}"
LABELS_FILE="${LABELS_FILE:-$HERE/labels.yaml}"

say() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY_RUN" = "1" ]; then
    say "[dry-run] $*"
  else
    "$@"
  fi
}

command -v gh >/dev/null || { say "gh is not installed" >&2; exit 1; }
# shellcheck disable=SC2086
$PYTHON -c "import yaml" 2>/dev/null || { say "PyYAML is not available to: $PYTHON" >&2; exit 1; }

if [ "$DRY_RUN" = "1" ]; then
  say "DRY_RUN=1: nothing will be created. Re-run with DRY_RUN=0 to apply to $REPO."
fi

# --- helpers that read the YAML files (one tiny Python call each; no jq needed) ----------------
py() { $PYTHON -c "$@"; }

label_count="$(py "import yaml,sys; print(len(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))))" "$LABELS_FILE")"
issue_count="$(py "import yaml,sys; print(len(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))))" "$ISSUES_FILE")"

# --- 1. labels ---------------------------------------------------------------------------------
say "== labels ($label_count) =="
for ((i = 0; i < label_count; i++)); do
  name="$(py "import yaml,sys; print(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))[int(sys.argv[2])]['name'])" "$LABELS_FILE" "$i")"
  color="$(py "import yaml,sys; print(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))[int(sys.argv[2])]['color'])" "$LABELS_FILE" "$i")"
  desc="$(py "import yaml,sys; print(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))[int(sys.argv[2])]['description'])" "$LABELS_FILE" "$i")"
  run gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" --force
done

# --- 2. issues ---------------------------------------------------------------------------------
say "== issues ($issue_count) =="
existing="$(gh issue list --repo "$REPO" --state all --limit 1000 --json title --jq '.[].title' || true)"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

created=0
skipped=0
for ((i = 0; i < issue_count; i++)); do
  title="$(py "import yaml,sys; print(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))[int(sys.argv[2])]['title'])" "$ISSUES_FILE" "$i")"
  if printf '%s\n' "$existing" | grep -Fxq -- "$title"; then
    say "skip (exists): $title"
    skipped=$((skipped + 1))
    continue
  fi
  body_file="$tmpdir/body-$i.md"
  py "import yaml,sys; sys.stdout.write(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))[int(sys.argv[2])]['body'])" "$ISSUES_FILE" "$i" >"$body_file"
  labels="$(py "import yaml,sys; print(','.join(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))[int(sys.argv[2])]['labels']))" "$ISSUES_FILE" "$i")"
  say "create: $title  [labels: $labels]"
  # gh accepts a comma-separated --label value; label names here contain no commas.
  run gh issue create --repo "$REPO" --title "$title" --body-file "$body_file" --label "$labels"
  created=$((created + 1))
done

say "done: $created to create, $skipped skipped (DRY_RUN=$DRY_RUN)"
