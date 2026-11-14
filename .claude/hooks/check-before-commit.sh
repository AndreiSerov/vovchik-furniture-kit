#!/usr/bin/env bash
# Blocks `git commit`, in any form, until scripts/check passes in the repository it commits to.
# It runs on every Bash call and finds the commit itself, because a settings `if` rule misses
# `git -C . commit`, `git -c a=b commit` and `sh -c "git commit"`.
# The commit's folder starts from the hook's cwd field, because CLAUDE_PROJECT_DIR stays on the
# main checkout when the session works in a worktree, then follows any `cd dir` before the commit
# and any `git -C dir`. A commit to another repository is checked there, not here. A folder that
# can't be resolved leaves the check on the folder before it.
set -u

folders=$(python3 -c '
import json, os, re, sys
from pathlib import Path

event = json.load(sys.stdin)
command = event.get("tool_input", {}).get("command", "")
start = Path(event.get("cwd", "."))


def step(folder, word):
    target = folder / os.path.expanduser(word.strip("\"\x27"))
    return target if target.is_dir() else folder


# git, then any global options with their values, then the commit subcommand.
options = r"(?:\s+-[cC]\s+\S+|\s+--?[\w-]+(?:=\S+)?)*"
seen = []
for found in re.finditer(rf"\bgit({options})\s+commit(?![\w-])", command):
    folder = start
    for word in re.findall(r"(?:^|[;&|(\n])\s*cd\s+(\S+)", command[: found.start()]):
        folder = step(folder, word)
    for word in re.findall(r"-C\s+(\S+)", found.group(1)):
        folder = step(folder, word)
    if str(folder) not in seen:
        seen.append(str(folder))
print("\n".join(seen))
')
[ -n "$folders" ] || exit 0

while IFS= read -r folder; do
  root=$(git -C "$folder" rev-parse --show-toplevel 2>/dev/null) || continue
  if [ ! -x "$root/scripts/check" ]; then
    # A repo this template didn't generate has nothing to enforce.
    [ -f "$root/.copier-answers.yml" ] || continue
    echo "Commit blocked: scripts/check is missing or not executable in $root." >&2
    exit 2
  fi
  if ! output=$(cd "$root" && scripts/check 2>&1); then
    printf 'Commit blocked: scripts/check failed in %s.\n%s\n' "$root" "$(printf '%s\n' "$output" | tail -n 40)" >&2
    exit 2
  fi
done <<EOF
$folders
EOF
exit 0
