# Issue tracker: GitHub

Issues and specs live in this repository's GitHub Issues. Use the `gh` CLI.

- "Publish to the issue tracker" means `gh issue create`.
- "Fetch the relevant ticket" means `gh issue view <number> --comments`.
- A spec is an issue, and its tickets are issues that link back to it.
- Record triage state with labels (see `triage-labels.md`), and blocking with a `Blocked by: #N` line in the body.
