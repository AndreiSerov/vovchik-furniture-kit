# Workflow

Features follow Matt Pocock's skills flow. `/ask-matt` routes anything that doesn't fit, such as upkeep; hard bugs start at `diagnosing-bugs`.

The user starts each `/`-prefixed step, because the agent can't invoke those skills.

1. **Grill**: `/grill-with-docs` asks about every decision until none is left open. New terms go in `GLOSSARY.md`, and lasting decisions in `docs/adr/`.
2. **Spec**: `/to-spec` turns the grilling into a spec, and the user confirms its test seams.
3. **Tickets**: `/to-tickets` splits a multi-session build into tracer-bullet tickets with blocking edges. Work that fits one session goes straight to step 4. Then the `reviewer` subagent checks **coverage** once: every user story and brief item lands in a ticket, the blocking edges hold, and each ticket fits one session.
4. **Build**: `/implement` runs per ticket, with a cleared context between tickets, or `/implement-spec` runs the whole spec on one integration branch. Implementers drive `tdd`, one red-green slice at a time.
5. **Review**: `code-review` checks the diff against the standards and the spec, and every finding is fixed or reported. The rounds follow [Review rounds](#review-rounds).
6. **Ship**: push the feature branch and open a PR with `pr`. The user merges.
7. **Retro**: `/retro` runs before the session is cleared, with `scripts/session-stats` for the numbers. Mark each finding `project` or `harness`. Apply `project` findings here, and send `harness` findings as a change to the template named in `.copier-answers.yml`, so other repositories get them through `copier update`.

Keep steps 1-3 in one unbroken context, so the spec and tickets build on the grilling.

## Who does what

- The main session on the strongest model grills, specs, reviews and verifies the result.
- The `implementer` subagent builds on Sonnet, and the `reviewer` subagent reviews each step independently.
- Before review, check that the commit hash the implementer reports exists and that `scripts/check` passes on it. A hand-back without a commit goes straight back.
- Work from any model passes the same `scripts/check` and review. A ticket that fails review goes back to its implementer, at most three times. After the third failed review, stop and report what keeps failing to the user, because the ticket or the environment may be wrong.
- One ticket, one agent: the implementer takes it with `scripts/claim`, and the main session releases it with `scripts/claim <ticket> --release` once the ticket passes review, or before it hands the ticket to a fresh implementer, who claims it again.

## Review rounds

Most of a ticket's time goes to rounds of review and fixes, not to the first build. Each rule below removes a round.

- **Readiness before the build.** Hand the ticket to the `reviewer` subagent for a readiness review: it reads the ticket cold against its spec, `DECISIONS.md` and the code it builds on, and returns open decisions (where each input comes from, behaviour at the edges, every entry of **Вывод**) as questions for the user with two or three options each, plus missing acceptance checks. The coordinator wrote the ticket and shares its blind spots, so this read comes from a fresh context. Ask the user the questions, then write the answers and the acceptance checks into the ticket (format: `issue-tracker.md`). The ticket is ready when **Вывод** holds no «ПРОВЕРИТЬ МАСТЕРУ» and every acceptance check names its input. A check that recurs across tickets becomes a test or a tool in the project, not an instruction.
- **One report, one focused fix commit.** The reviewer sorts findings into blocking, test gaps and deferred (`.claude/agents/reviewer.md`). Send the implementer the blocking findings and test gaps in one message; deferred findings go straight to the end-of-stage list, so a fix commit stays small and the next review has less new code to break. A re-review is a full review again, since fixes break things and the first review misses some; it checks that blocking findings and test gaps are closed, and deferred findings never come back as blocking. A ticket with no blocking finding is accepted: the coordinator adds the ready-made tests for the test gaps itself instead of opening another round. At the end of the stage, one cleanup ticket works through the deferred list.
- **Fresh context on the second return.** The first return goes to the same implementer, who still knows the code. From the second return on, or once its context passes about 150k tokens, or when the user changes a decision on the ticket, start a new implementer. Hand it the commit hash, the findings, the reviewer's reproductions and the related entries of the decision log.
- **Keep building during review.** While the reviewer reads ticket N, a second implementer can take the next ticket that doesn't depend on N, in its own worktree.
- **Stronger model for hard tickets.** For an algorithm or layout ticket, where reviews keep finding edge cases, run the implementer on the main session's model and compare the rounds in `scripts/session-stats`.

## Harness upkeep

- `scripts/sync-skills` installs the pinned skills after a clone. `scripts/sync-skills --update` pulls newer ones; review the printed changes before committing the moved pin.
- The `skill-scout` subagent proposes external skills for this project's stack and domain, and installs nothing. To accept one, read the proposal, add its entry to `.claude/skills.toml`, run `scripts/sync-skills`, then read the installed skill before committing the pin.
- `copier update` brings in template changes for the files the template owns.
- `scripts/doctor` reports anything missing or out of date.
