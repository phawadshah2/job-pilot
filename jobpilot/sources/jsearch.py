"""JSearch — aggregates Google for Jobs, which itself indexes LinkedIn, Indeed,
Bayt, GulfTalent, Glassdoor and company sites. This is the main source for
onsite UAE / Saudi roles.

Two providers, same data:
  - OpenWeb Ninja (JSearch's own portal):  OPENWEBNINJA_API_KEY   <- preferred
  - RapidAPI listing (legacy):             RAPIDAPI_KEY
Free tier on both: 200 requests/month (hard cap, no overage charges). We spend
one request per query per run: 3 queries/day ≈ 90/month.
"""
from __future__ import annotations

import re
import urllib.parse

from ..http import get_json
from ..models import Job
from ..text import clean_location, parse_date

OWN_URL = "https://api.openwebninja.com/jsearch/search-v2"
RAPID_HOST = "jsearch.p.rapidapi.com"

# Application links we can pre-fill in step 3, best first.
_ATS = re.compile(r"greenhouse\.io|jobs\.lever\.co|ashbyhq\.com|apply\.workable\.com|smartrecruiters\.com", re.I)
_OPEN_ONLY = re.compile(r"linkedin\.com|indeed\.|glassdoor\.|bayt\.com|naukrigulf\.com", re.I)


# Used when JSearch leaves a job's location blank but the search was country-scoped.
COUNTRY_NAMES = {"ae": "United Arab Emirates", "sa": "Saudi Arabia", "qa": "Qatar", "kw": "Kuwait",
                 "bh": "Bahrain", "om": "Oman", "gb": "United Kingdom", "us": "United States", "au": "Australia"}


def endpoint(env: dict) -> tuple[str, dict[str, str]] | None:
    if env.get("OPENWEBNINJA_API_KEY"):
        return OWN_URL, {"x-api-key": env["OPENWEBNINJA_API_KEY"]}
    if env.get("RAPIDAPI_KEY"):
        return f"https://{RAPID_HOST}/search", {"X-RapidAPI-Key": env["RAPIDAPI_KEY"], "X-RapidAPI-Host": RAPID_HOST}
    return None


def jsearch(cfg: dict) -> list[Job]:
    ep = endpoint(cfg["env"])
    if not ep:
        print("    - skipped: set OPENWEBNINJA_API_KEY in .env (free at openwebninja.com/api/jsearch)")
        return []
    url, headers = ep
    js = cfg["jsearch"]
    usage = cfg.get("_usage")  # Store-backed counter injected by the CLI (None in tests)
    budget = js.get("monthly_budget", 190)
    jobs: list[Job] = []
    for q in js["queries"]:
        if usage and usage.month_count("jsearch") >= budget:
            print(f"    - monthly budget reached ({budget} requests): skipping remaining queries")
            break
        params = {"query": q["query"], "num_pages": 1, "date_posted": js.get("date_posted", "week")}
        if q.get("country"):
            params["country"] = q["country"]
        if q.get("remote_only"):
            params["work_from_home"] = "true"
        if js.get("exclude_publishers"):
            params["exclude_job_publishers"] = ",".join(js["exclude_publishers"])
        resp = get_json(f"{url}?" + urllib.parse.urlencode(params), headers=headers)
        if usage:
            usage.record("jsearch")
        found = parse(resp, default_country=None if q.get("remote_only") else q.get("country"))
        print(f"    {len(found):>3} for '{q['query']}'")
        jobs += found
    return jobs


def parse(resp, default_country: str | None = None) -> list[Job]:
    data = (resp or {}).get("data") or []
    items = data.get("jobs", []) if isinstance(data, dict) else data   # search-v2 vs legacy /search
    jobs = []
    for j in items:
        loc = ", ".join(x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x)
        apply = best_apply_link(j)
        jobs.append(Job(
            source=f"jsearch:{(j.get('job_publisher') or '').lower()}",
            title=j.get("job_title", ""),
            company=j.get("employer_name", ""),
            url=apply or j.get("job_google_link", ""),
            apply_url=apply,
            location=clean_location(_with_country(loc or j.get("job_location", "") or "", default_country)),
            remote=bool(j.get("job_is_remote")) or j.get("work_arrangement") == "remote",
            posted_at=parse_date(j.get("job_posted_at_datetime_utc") or j.get("job_posted_at_timestamp")),
            description=j.get("job_description", "") or "",
            employment_type=j.get("job_employment_type", "") or "",
            salary=j.get("job_salary_string", "") or "",
            notes=["hybrid"] if j.get("work_arrangement") == "hybrid" else [],
        ))
    return jobs


def _with_country(location: str, country: str | None) -> str:
    name = COUNTRY_NAMES.get(country or "")
    if not name or name.lower() in location.lower():
        return location
    return f"{location}, {name}" if location else name


def best_apply_link(j: dict) -> str:
    """Prefer the employer's own ATS form (pre-fillable), then any direct
    link, then anything that isn't a ToS-restricted board, then the default."""
    options = [o for o in (j.get("apply_options") or []) if o.get("apply_link")]
    default = j.get("job_apply_link", "") or ""
    for pick in (
        lambda o: _ATS.search(o["apply_link"]),
        lambda o: o.get("is_direct"),
        lambda o: not _OPEN_ONLY.search(o["apply_link"]),
    ):
        for o in options:
            if pick(o):
                return o["apply_link"]
    return default
