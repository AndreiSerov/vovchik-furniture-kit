#!/usr/bin/env bash
# Blocks in-place stream edits (`sed -i`, `perl -i`) of tracked files.
# They differ between GNU and BSD: on macOS `sed -i` with the wrong argument can empty a file
# silently. Edit, `sd` or `ast-grep` do the same job safely and the same on every system.
# Scratch files outside git stay editable.
set -u

python3 -c '
import json, re, shlex, subprocess, sys
from pathlib import Path

event = json.load(sys.stdin)
command = event.get("tool_input", {}).get("command", "")
cwd = Path(event.get("cwd", "."))

if not re.search(r"\b(sed|perl)\s+(-\S*\s+)*-\w*i", command):
    sys.exit(0)

root = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
if root.returncode:
    sys.exit(0)
root = Path(root.stdout.strip())
tracked = set(subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True).stdout.split())

try:
    words = shlex.split(command, posix=True)
except ValueError:
    words = command.split()

# A `cd dir && ...` prefix moves the relative paths that follow it.
bases = [cwd] + [cwd / w for a, w in zip(words, words[1:]) if a == "cd"]
hits = set()
for word in words:
    for base in bases:
        try:
            rel = (base / word).resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            continue
        if rel in tracked:
            hits.add(rel)
if hits:
    print(
        "Blocked: in-place sed/perl on tracked files (" + ", ".join(sorted(hits)) + "). "
        "Use Edit for a targeted change, `sd -p` then `sd` for a text pattern, "
        "or `ast-grep` for a structural change of code.",
        file=sys.stderr,
    )
    sys.exit(2)
'
