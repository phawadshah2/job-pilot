# job-pilot

Find jobs for **your** role across job boards, company career pages, Google Jobs (LinkedIn/Indeed
listings) and your **job-alert emails**, score them against your skills, tailor an ATS-friendly CV
for each good match, and pre-fill the application forms. **You always click Submit.**

Works for any tech role (Flutter, iOS, backend, data, …) and any mix of regions, ranked in the order
you choose. Free to run: every source has a free tier. Designed to be driven by
[Claude Code](https://claude.com/claude-code): say **"find jobs"**, then **"apply"**.

```
find:  job boards + career pages + JSearch + Gmail alerts → filter → score (your region priority first)
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
| **Google Sheet** (optional) | A tracker you can sort and filter (Location, Work mode, Source, Status…) | Paste `apps_script/Code.gs` into Extensions → Apps Script, set the TOKEN that setup prints, Deploy → Web app (Execute as: Me, Access: Anyone), give setup the `/exec` URL |
| **Gmail app password** (optional) | Reads LinkedIn/Indeed job-alert emails for free, marks them read | Google Account → Security → 2-Step Verification → App passwords |

## Use
With Claude Code in this folder, say **"find jobs"** (playbook: [FIND.md](FIND.md)), then **"apply"**.
By hand:
```bash
python3 -m jobpilot find                    # fetch + filter + score + prepare; prints a compact summary
python3 -m jobpilot build <key>             # tailor.json -> CV -> checks -> PDFs (see TAILOR.md)
.venv/bin/python -m jobpilot apply          # pre-fill each Ready job; you click Submit
python3 -m jobpilot followups               # applications due for a follow-up
python3 -m jobpilot add --url URL --company X --title Y --jd jd.txt   # a job you found yourself
python3 -m jobpilot sheet-reset             # empty the Google Sheet (asks first)
```

## What's automated, and what never is
- **Never automated:** clicking Submit; LinkedIn / Indeed / Glassdoor / Bayt / NaukriGulf forms (opened in
  your normal browser instead, since automating them risks your account); CAPTCHAs and security checks;
  consent boxes and demographic questions.
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
