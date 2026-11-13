# Issue tracker: local files, GitHub Issues for bugs

See ADR `docs/adr/0004-local-tracker.md`.

## Specs and tickets

- A spec is a file `docs/specs/stage-N-<name>.md`, one per stage. Its status line holds the triage label, such as `ready-for-agent`.
- Tickets are `## Тикет N.M — <title>` sections inside their spec, keeping the task numbers from the original brief. Blocking is a `Blocked by: N.K` line in the ticket.
- "Publish to the issue tracker" means writing or updating these files.
- "Fetch the relevant ticket" means reading its section in the spec file.
- `PROGRESS.md` at the repo root is the source of truth for ticket status. Mark a ticket done there in the same commit that finishes it.

## Bugs

Bugs found on the remote live in this repository's GitHub Issues. Use the `gh` CLI: `gh issue create` to file one and `gh issue view <number> --comments` to read it. Record triage state with labels (see `triage-labels.md`).
