#!/usr/bin/env bash
# Sends edits of tracked files to the Edit and Write tools instead of Bash.
# Agents that rewrite files through `python3 - <<EOF`, `sed -i` or `cat > file` emit whole
# scripts in one response, which gets cut off mid-stream and costs a full retry.
# Scratch files outside git stay writable from Bash.
set -u

python3 -c '
import json, re, shlex, subprocess, sys
from pathlib import Path

event = json.load(sys.stdin)
command = event.get("tool_input", {}).get("command", "")
cwd = Path(event.get("cwd", "."))

inline_script = re.search(r"\b(python3?|perl|ruby|node)\b[^\n|;&]*\s(-\s*<<|-c\s|-e\s)", command)
writes_in_script = re.search(r"write_text|write_bytes|\.write\(|open\([^)]*[\x27\"][wa]", command)
in_place = re.search(r"\b(sed|perl)\s+(-\w*\s+)*-\w*i", command)
redirect_targets = re.findall(r"(?:^|[^<>&\d])>>?\s*([^\s;&|<>]+)", command)
redirect_targets += re.findall(r"\btee\s+(?:-a\s+)?([^\s;&|<>]+)", command)

if not (inline_script and writes_in_script) and not in_place and not redirect_targets:
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
candidates = set(re.findall(r"[\x27\"]([^\x27\"\s]+)[\x27\"]", command)) | set(words) | set(redirect_targets)
if not (inline_script and writes_in_script) and not in_place:
    candidates = set(redirect_targets)

hits = []
for word in candidates:
    try:
        path = (cwd / word).resolve()
        rel = path.relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        continue
    if rel in tracked:
        hits.append(rel)
if hits:
    print(
        "Blocked: this Bash command writes tracked files (" + ", ".join(sorted(hits)) + "). "
        "Change them with the Edit tool, one small edit per call, or Write for a whole new file.",
        file=sys.stderr,
    )
    sys.exit(2)
'
