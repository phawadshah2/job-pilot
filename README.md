# job-pilot

Find jobs for **your** role across company career pages, well-known remote boards, Google Jobs and
your **job-alert emails** (LinkedIn, Indeed, Bayt, NaukriGulf, GulfTalent, Wellfound, ...), score
them against your skills, tailor an ATS-friendly CV for each good match, and pre-fill the application
forms. Re-posting aggregators are dropped. **You always click Submit.**

Works for any tech role (Flutter, iOS, backend, data, …) and any mix of regions, ranked in the order
you choose. Free to run: every source has a free tier. Designed to be driven by
[Claude Code](https://claude.com/claude-code): say **"find jobs"**, then **"apply"**.

```
find:  career pages + remote boards + JSearch + alert emails → drop aggregators → filter → score (region priority first)
       → prepare top matches → Claude writes tailor.json → CV + JD PDFs (one clean design) → Google Sheet
apply: each Ready job opens pre-filled with its CV → you review and click Submit → sheet says "Applied"
```

## Requirements
- Python 3.9+ (standard library only for everything except `apply`)
- Google Chrome or Chromium (PDFs, and the form filler)
- poppler (`pdftotext`, `pdffonts`): `brew install poppler` / `apt install poppler-utils`
- For `apply`: `python3 -m venv .venv && .venv/bin/pip install playwright`

## Setup (about 15 minutes, all in the terminal)
```bash
git clone <this repo> && cd job-pilot
python3 -m jobpilot setup          # asks everything; safe to re-run, current answers are defaults
python3 -m jobpilot doctor         # what's set up / missing
```
`setup` asks about you, the role you want, where (regions in priority order, remote, languages,
salary floor), your skills (and the ones you **don't** have, so they never land on your CV), your CV
content, answers for application forms, and optional connections. Re-run a single part with
`--section about|role|where|skills|cv|answers|connect`.

**Your data never lives in this repo.** Everything personal goes to `~/.job-pilot/` (override with
`JOBPILOT_HOME`): `user.json`, `skills.json`, `cv.json`, `applicant.json`, `facts.md`, `.env`
(secrets), `jobs/` (one folder per job) and `data/` (database). See `examples/alex/` for a complete
fictional profile.

### Connections (`setup --section connect`)
| What | Why | How |
|---|---|---|
| **JSearch** API key | Main source: Google for Jobs (LinkedIn, Indeed, Glassdoor, company sites) | Free plan (200 requests/month, hard cap) at openwebninja.com/api/jsearch |
| **Google Sheet** (optional) | A tracker with score, match explanation, missing requirements, salary, CV/cover letter and status; filter by Location, Work mode, Source, Status… | Paste `apps_script/Code.gs` into Extensions → Apps Script, set the TOKEN that setup prints, Deploy → Web app (Execute as: Me, Access: Anyone), give setup the `/exec` URL |
| **Gmail app password** (optional) | Reads LinkedIn/Indeed job-alert emails for free, marks them read | Google Account → Security → 2-Step Verification → App passwords |

## Use
With Claude Code in this folder, say **"find jobs"**: you get every reviewed job in one list, tick the
ones to keep, and only those go to the sheet. Then say **"apply"**: one job at a time, a tailored CV
(and cover letter) plus the link and ready answers; you apply and say "done" (playbook: [FIND.md](FIND.md)).
By hand:
```bash
python3 -m jobpilot find                    # collect + clean + pre-score; prints jobs to review
python3 -m jobpilot jd <key>...             # read a candidate's JD
python3 -m jobpilot assess --file a.json    # record AI score/explanation/missing (no status change)
python3 -m jobpilot pick <keep keys>...     # your choice: kept -> Approved + sheet, the rest skipped
python3 -m jobpilot next                    # the next job to apply to
python3 -m jobpilot prepare <key>           # job folder for tailoring
python3 -m jobpilot build <key>             # tailor.json -> CV (+ cover letter) PDFs
python3 -m jobpilot status <key> Submitted  # after you applied (or Pending / Failed / Skipped)
python3 -m jobpilot followups               # applications due for a follow-up
python3 -m jobpilot sheet-reset             # empty the Google Sheet (asks first)
```
Statuses: New → Approved (you kept it) → Pending / Failed / Manual action required → **Submitted**
(only after you confirm) → Interview → Offer / Rejected / Skipped.

## Where jobs come from, and where you apply
Every job's link is sorted (`jobpilot/sites.py`) and shown in the sheet's **Apply via** column:

| Apply via | Examples | What happens |
|---|---|---|
| Company form (no account) | Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Teamtailor, Breezy, the company's own site | Pre-filled for you |
| Remote board → company | Himalayas, Remotive, RemoteOK, Jobicy, We Work Remotely | Board page, then the company's form |
| Your account | LinkedIn, Indeed, Bayt, NaukriGulf, GulfTalent, foundit Gulf, Wellfound, Welcome to the Jungle, Arc, Turing, Glassdoor | Opened in your own browser |
| Needs account | Workday, SuccessFactors, Taleo, Oracle Cloud, iCIMS (one account per company) | Opened in your own browser |
| Aggregator | Jooble, JobLeads, Trabajo, beBee, Jobrapido, Upwork, ... | Dropped |

Platforms without a public feed (Bayt, Wellfound, ...) come in through their **job-alert emails**:
turn alerts on, sent to the Gmail that job-pilot reads. Only **unread** alerts from the last 7 days are
read (every job in the email, not the "see all jobs" page behind it); emails with jobs are then marked
read, others stay unread.

Remote jobs listed for a country you don't live in (`home_country`) are dropped when the title or JD
spells out a residency rule ("[US]", "must be based in", "authorized to work in", "Remote (India)"...),
unless it also says worldwide / your region. A board's location label alone only adds a
"check it's open to you" note.

## What's automated, and what never is
- **Never automated:** clicking Submit; job platforms you sign in to and per-company account portals
  (opened in your normal browser instead, since automating them risks your account); CAPTCHAs and
  security checks (a site showing one is handed to your browser); consent boxes and demographic questions.
- **CV honesty:** `finalize` refuses a CV that mentions a skill you said you don't have, and tailoring can
  only change text (the design and your facts come from your own files).

## Scoring (0-100)
Role keyword focus 25 · your skills vs the JD 30 · seniority 15 · years asked vs yours 15 · region
(your priority order) 15 · minus up to 30 for hard gaps. Deal-breakers (languages you don't speak,
"based in" a country you didn't pick, other primary stack, citizenship) drop a job before scoring.

## Tuning
Personal choices: `python3 -m jobpilot setup --section …`. Shared defaults (sources, spam blocklist,
region catalog, JSearch budget): `config.json`. Skill names and how JDs phrase them:
`jobpilot/data/skills_catalog.json`. Pull requests welcome.

## Tests
```bash
.venv/bin/python -m unittest discover -s tests -t .   # all, incl. headless-Chrome form tests
python3 -m unittest discover -s tests -t .            # without Playwright (browser tests skip)
```
Tests only use the fictional profile in `examples/alex/`.

## License
MIT, see [LICENSE](LICENSE).
