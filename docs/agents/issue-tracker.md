# Issue tracker: local files, GitHub Issues for bugs

See ADR `docs/adr/0004-local-tracker.md`.

## Specs and tickets

- A spec is a file `docs/specs/stage-N-<name>.md`, one per stage. Its status line holds the triage label, such as `ready-for-agent`.
- Tickets are `## Тикет NN — <title> (<task numbers from the original brief>)` sections inside their spec, numbered through all specs. Blocking is a `Blocked by: NN, MM` line in the ticket.
- A ticket ready for the build carries **Проверки приёмки**: the checks the reviewer will run, each one a test the implementer writes first.
- A ticket that produces something a person reads (a document for the client, a map or sheet for the shop, CLI text) also carries **Вывод**: every field and line of that output, its wording, what it shows for empty, edge and overridden values, and for a drawing which side is shown. Each entry is a decision of the user; an entry the agent chose itself is marked «ПРОВЕРИТЬ МАСТЕРУ» and keeps the ticket out of the build.
- "Publish to the issue tracker" means writing or updating these files.
- "Fetch the relevant ticket" means reading its section in the spec file.
- `PROGRESS.md` at the repo root is the source of truth for ticket status. Mark a ticket done there in the same commit that finishes it.

## Bugs

Bugs found on the remote live in this repository's GitHub Issues. Use the `gh` CLI: `gh issue create` to file one and `gh issue view <number> --comments` to read it. Record triage state with labels (see `triage-labels.md`).
