# Current state

_Last updated: 2026-10-26_

**Goal:** daily, low-token job search for one user: "find jobs" (collect → score → user picks) and
"apply" (one job at a time, CV tailored on demand). Workflow: [FIND.md](../FIND.md),
tailoring: [TAILOR.md](../TAILOR.md), interviews: [INTERVIEW.md](../INTERVIEW.md).

## Done (merged to `main`, PRs #1–#4)
- Trusted sources only; aggregators dropped; "Apply via" sheet column.
- User picks every job (`find` → `assess` → `pick`); only kept jobs reach the sheet.
- One-at-a-time apply (`next` → `prepare` → `build`); status never moves backwards.
- Residency rule for remote jobs (explicit limits dropped, label-only flagged).
- Alert parsers: LinkedIn, Indeed (incl. sponsored), GulfTalent, SEEK, Reed, Dice, generic link parser.
- Reviewer pass (`critique`, runs after `build`); Gmail status sync (`inbox`, read-only, runs in
  `find`); interview prep (`prep`, `--pdf`).

## Checks
- `.venv/bin/python -m unittest discover -s tests -t .`: 137 tests OK on 2026-10-21
  (feat/review-inbox-interview, before merge). Not run since the merge.

## Open
- 2 inbox proposals pending ("received" confirmations): apply only after the user says yes.
- Follow-ups were due 2026-10-24 and 2026-10-26 (`jobpilot followups`).
- Bayt / GulfTalent / NaukriGulf job alerts not yet arriving: the main gap for UAE roles.
- Add `jobg8.com` to the aggregator list in `jobpilot/sites.py` (seen 2026-10-21).
- Optional cleanup: stale wording in `cmd_review` ("pre-filled", "Approve with").

## Next action
User says "find jobs" → follow FIND.md (inbox replies first). Read: FIND.md, `jobpilot/__main__.py`
(cmd_find, cmd_inbox) only if something fails.
