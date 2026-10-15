"""`python3 -m jobpilot setup`: everything personal is asked here, in the terminal, and
written to ~/.job-pilot (never the repo). Safe to re-run: current answers are shown as
defaults; press Enter to keep them. Run one part with --section NAME.

Sections: about, role, where, skills, cv, answers, connect.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import secrets
from datetime import date
from pathlib import Path

from . import paths

SECTIONS = ["about", "role", "where", "skills", "cv", "answers", "connect"]
CATALOG = paths.REPO / "jobpilot" / "data" / "skills_catalog.json"


# ------------------------------------------------------------- pure builders --
# (no input/output: unit-tested, and reused to generate the example user)

def load_catalog() -> dict[str, list[str]]:
    return {k: v for k, v in json.loads(CATALOG.read_text()).items() if not k.startswith("_")}


def match_skill(name: str, catalog: dict) -> str:
    """Catalog key for a typed skill name (case-insensitive), else the name as typed."""
    low = name.strip().lower()
    for k in catalog:
        if k.lower() == low:
            return k
    return name.strip()


def build_skills(have: list[str], dont: list[str], unsure: list[str], catalog: dict,
                 allowed_phrases: dict | None = None) -> dict:
    """skills.json: what you have (scoring), what you DON'T have (gaps + never on the CV),
    and what you're unsure about (flagged, never claimed)."""
    def group(names):
        out = {}
        for n in names:
            key = match_skill(n, catalog)
            if key:
                out[key] = catalog.get(key, [key.lower()])
        return out
    gaps = group(dont)
    forbidden = []
    for pats in gaps.values():
        for p in pats:
            if p.startswith("cs:") or p.startswith("re:"):
                forbidden.append(p[3:] if p[3:].startswith("\\b") else p[3:])
            else:
                forbidden.append(p)
    return {
        "have": group(have),
        "gap": gaps,
        "unverified": group(unsure),
        "forbidden_in_cv": {"patterns": forbidden, "allowed_phrases": allowed_phrases or {}},
    }


def bullet_ids(bullets: list[str]) -> dict[str, str]:
    """Readable, unique ids from each bullet's first word ('Built a ...' -> 'built')."""
    out: dict[str, str] = {}
    for b in bullets:
        word = re.sub(r"[^a-z]", "", (b.split() or ["item"])[0].lower())[:14] or "item"
        k, i = word, 2
        while k in out:
            k, i = f"{word}{i}", i + 1
        out[k] = b
    return out


def build_facts(user: dict, skills: dict, cv: dict) -> str:
    """facts.md: everything Claude may use when tailoring, in one readable page."""
    role = user["role"]
    years = _years(role.get("career_start"))
    lines = [f"# Facts for tailoring: {user['name']}", "",
             "Source of truth for CV tailoring. Never claim anything not listed here.", "",
             f"- Target role: {role['target_title']} ({years}+ years since {role.get('career_start', '?')})",
             f"- Regions, in priority order: {', '.join(user['search']['regions'])}",
             f"- Languages spoken: {', '.join(user['search'].get('languages', [])) or 'not given'}", "",
             "## Skills they HAVE", ", ".join(skills["have"]) or "-", "",
             "## Skills they DON'T have: never claim (finalize refuses them)", ", ".join(skills["gap"]) or "-", ""]
    if skills.get("unverified"):
        lines += ["## Unsure: don't claim, ask them", ", ".join(skills["unverified"]), ""]
    allowed = skills.get("forbidden_in_cv", {}).get("allowed_phrases", {})
    if allowed:
        lines += ["## Allowed exceptions", *[f"- '{k}' only inside '{v}'" for k, v in allowed.items()], ""]
    lines += ["## Experience (bullet ids for tailor.json)"]
    for job in cv.get("experience", []):
        lines.append(f"\n### {job['title']} | {job['company']} ({job.get('dates', '')})  id: `{job['id']}`")
        lines += [f"- `{k}`: {v}" for k, v in job.get("bullets", {}).items()]
    if cv.get("projects"):
        lines += ["", "## Projects"] + [f"- `{p.get('id', '')}` {p['name']}: {p.get('tech', '')}" for p in cv["projects"]]
    if cv.get("competencies"):
        lines += ["", "## Competency lines (ids for tailor.json)"] + \
                 [f"- `{k}` {v['label']}: {v['text']}" for k, v in cv["competencies"].items()]
    lines += ["", "Numbers/metrics may only be used exactly as they appear above."]
    return "\n".join(lines) + "\n"


def _years(start: str | None) -> int:
    if not start or not re.match(r"\d{4}-\d{2}", start):
        return 0
    y, m = map(int, start[:7].split("-"))
    t = date.today()
    return max(0, (t.year - y) - (1 if t.month < m else 0))


def write_env(updates: dict[str, str], path: Path | None = None) -> None:
    """Merge keys into ~/.job-pilot/.env (chmod 600). Empty values never overwrite existing ones."""
    p = path or paths.env_file()
    lines = p.read_text().splitlines() if p.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in updates:
            seen.add(k)
            if updates[k]:
                lines[i] = f"{k}={updates[k]}"
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen and v]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")
    os.chmod(p, 0o600)


# ------------------------------------------------------------------ prompts --

def ask(q: str, default: str = "", required: bool = False) -> str:
    while True:
        a = input(f"{q}{f' [{default}]' if default else ''}: ").strip()
        if a or default or not required:
            return a or default
        print("  (required)")


def ask_list(q: str, default: list | None = None) -> list[str]:
    d = ", ".join(default or [])
    a = ask(f"{q} (comma-separated)", d)
    return [x.strip() for x in a.split(",") if x.strip()]


def ask_yes(q: str, default: bool = False) -> bool:
    a = ask(f"{q} (y/n)", "y" if default else "n").lower()
    return a.startswith("y")


def ask_lines(q: str, default: list | None = None) -> list[str]:
    """One item per line; an empty line ends. Enter on the first line keeps the current list."""
    if default:
        print(f"{q}: currently {len(default)} item(s). Press Enter to keep, or type new ones.")
    else:
        print(f"{q}: one per line, empty line to finish.")
    out = []
    while True:
        line = input("  > ").strip()
        if not line:
            break
        out.append(line)
    return out or list(default or [])


def _load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def _save(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.chmod(p, 0o600)


# ----------------------------------------------------------------- sections --

def s_about(user: dict) -> None:
    print("\n== About you ==")
    a = user.setdefault("about", {})
    user["name"] = ask("Full name", user.get("name", ""), required=True)
    for k, q in [("email", "Email"), ("phone", "Phone (with country code)"), ("city", "City you live in"),
                 ("country", "Country you live in"), ("linkedin", "LinkedIn URL (optional)"),
                 ("github", "GitHub URL (optional)"), ("portfolio", "Portfolio URL (optional)")]:
        a[k] = ask(q, a.get(k, ""), required=k in ("email", "city", "country"))


def s_role(user: dict) -> None:
    print("\n== The role you want ==")
    r = user.setdefault("role", {})
    r["target_title"] = ask("Job title you're targeting (e.g. Senior Flutter Developer)", r.get("target_title", ""), True)
    r["search_title"] = ask("Search phrase for job sites (e.g. flutter developer)", r.get("search_title", r["target_title"].lower()))
    r["keywords"] = ask_list("Keywords that MUST be in the job title (e.g. flutter, dart)",
                             r.get("keywords") or [r["search_title"].split()[0]])
    r["related_titles"] = ask_list("Broader titles that count if the JD mentions a keyword (e.g. mobile, app developer)",
                                   r.get("related_titles", []))
    r["competing_stacks"] = ask_list("Similar stacks to skip unless your keyword is also in the title (e.g. react native)",
                                     r.get("competing_stacks", []))
    r["exclude_title_stacks"] = ask_list("Other stacks that mean 'not my job' in a title (e.g. unity, sap)",
                                         r.get("exclude_title_stacks", []))
    r["seniority"] = ask("Your level: junior / mid / senior / lead", r.get("seniority", "senior"))
    r["career_start"] = ask("When did your career start? (YYYY-MM)", r.get("career_start", ""), True)


def s_where(user: dict, regions_catalog: dict) -> None:
    print("\n== Where ==")
    s = user.setdefault("search", {})
    print("Regions: " + ", ".join(f"{k} ({v['name']})" for k, v in regions_catalog.items() if not k.startswith("_"))
          + ", remote")
    s["regions"] = ask_list("Regions in PRIORITY order, best first (e.g. uae, gcc, remote, uk)", s.get("regions", []))
    s["home_country"] = ask("Your home country code for remote searches (e.g. ae, gb, us)", s.get("home_country", ""))
    s["remote_ok"] = ask_list("Remote regions/time zones you can work in besides worldwide (e.g. emea, gmt+4)",
                              s.get("remote_ok", []))
    s["languages"] = ask_list("Languages you speak (jobs requiring others are skipped)", s.get("languages", ["english"]))
    if ask_yes("Set a minimum salary? (lower offers get a 'low pay' note)", bool(s.get("salary_floor"))):
        cur = ask("Currency code (e.g. AED, USD, GBP)", (s.get("salary_floor") or {}).get("currency", ""))
        amt = ask("Minimum per month", str((s.get("salary_floor") or {}).get("per_month", "")))
        s["salary_floor"] = {"currency": cur.upper(), "per_month": int(re.sub(r"\D", "", amt) or 0)}
    else:
        s["salary_floor"] = None
    s["us_citizen"] = ask_yes("Are you a US citizen? (else 'US citizens only' jobs are skipped)", s.get("us_citizen", False))
    if ask_yes("Add company career boards to watch? (Greenhouse/Lever/Ashby/Workable/SmartRecruiters/Recruitee/Teamtailor/Breezy slugs)",
               any(s.get("companies", {}).values())):
        comp = s.setdefault("companies", {})
        for ats in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "teamtailor", "breezy"):
            comp[ats] = ask_list(f"  {ats} slugs", comp.get(ats, []))


def s_skills(skills_path: Path, catalog: dict, current: dict) -> dict:
    print("\n== Skills ==")
    print("Known skills (others are fine too): " + ", ".join(sorted(catalog, key=str.lower)))
    have = ask_list("Skills you HAVE", list(current.get("have", {})))
    dont = ask_list("Skills employers ask for that you DON'T have (never put on your CV)", list(current.get("gap", {})))
    unsure = ask_list("Skills you're unsure about (flagged, never claimed)", list(current.get("unverified", {})))
    skills = build_skills(have, dont, unsure, catalog, current.get("forbidden_in_cv", {}).get("allowed_phrases"))
    _save(skills_path, skills)
    return skills


def s_cv(cv: dict, user: dict) -> dict:
    print("\n== Your CV (rendered in the project's design; tailored per job later) ==")
    cv["name"] = ask("Name as it should appear (e.g. ALEX DOE)", cv.get("name", user.get("name", "").upper()))
    cv["headline"] = ask("Headline", cv.get("headline", user["role"]["target_title"]))
    a = user.get("about", {})
    cv["contact"] = [x for x in [f"{a.get('city', '')}, {a.get('country', '')}".strip(", "), a.get("phone"), a.get("email")] if x]
    cv["links"] = [re.sub(r"^https?://(www\.)?", "", x) for x in [a.get("linkedin"), a.get("github"), a.get("portfolio")] if x]
    cv["summary"] = ask("Professional summary (one paragraph)", cv.get("summary", ""), True)

    comp_lines = ask_lines("Competency lines as 'Label: item, item' (e.g. 'Languages: Dart, Kotlin')",
                           [f"{v['label']}: {v['text']}" for v in cv.get("competencies", {}).values()])
    comps = {}
    for line in comp_lines:
        label, _, text = line.partition(":")
        key = re.sub(r"[^a-z]", "", label.lower())[:12] or f"c{len(comps)}"
        comps[key] = {"label": label.strip(), "text": text.strip()}
    cv["competencies"], cv["competency_order"] = comps, list(comps)

    if not cv.get("experience") or ask_yes("Re-enter work experience?", False):
        cv["experience"] = []
        while ask_yes("Add a job (most recent first)?", not cv["experience"]):
            title = ask("  Job title", required=True)
            company = ask("  Company", required=True)
            bullets = bullet_ids(ask_lines("  Achievements (one per line, keep your real numbers)"))
            cv["experience"].append({
                "id": re.sub(r"[^a-z0-9]", "", company.lower())[:16] or f"job{len(cv['experience'])}",
                "title": title, "company": company, "location": ask("  Location"),
                "dates": ask("  Dates (e.g. Jan 2022 – Present)"), "bullets": bullets, "order": list(bullets)})
    if not cv.get("projects") or ask_yes("Re-enter projects?", False):
        cv["projects"] = []
        while ask_yes("Add a project?", False):
            name = ask("  Project name", required=True)
            cv["projects"].append({"id": re.sub(r"[^a-z0-9]", "", name.lower())[:16], "name": name,
                                   "subtitle": ask("  Short description"), "meta": ask("  Highlight (e.g. 50K+ users)"),
                                   "tech": ask("  Tech used"), "bullets": ask_lines("  Bullets")})
    if not cv.get("education") or ask_yes("Re-enter education?", False):
        cv["education"] = []
        while ask_yes("Add a degree?", not cv["education"]):
            cv["education"].append({"degree": ask("  Degree", required=True), "dates": ask("  Dates"),
                                    "school": ask("  School, city")})
    return cv


def s_answers(app: dict, user: dict, cv: dict) -> dict:
    print("\n== Answers for application forms (leave blank to answer on each form yourself) ==")
    a = user.get("about", {})
    first, _, last = user.get("name", "").partition(" ")
    job = (cv.get("experience") or [{}])[0]
    app.update({"first_name": first, "last_name": last, "full_name": user.get("name", ""), "email": a.get("email"),
                "phone": a.get("phone"), "city": a.get("city"), "country": a.get("country"),
                "linkedin": a.get("linkedin") or None, "github": a.get("github") or None,
                "portfolio": a.get("portfolio") or None, "current_company": job.get("company"),
                "current_title": job.get("title"), "years_experience": str(_years(user["role"].get("career_start")))})
    for k, q in [("notice_period", "Notice period (e.g. 1 month)"), ("salary_current", "Current salary (as you'd write it)"),
                 ("salary_expected", "Expected salary"), ("nationality", "Nationality"),
                 ("visa_status", "Visa / work-permit status"), ("relocate", "Willing to relocate? (Yes/No)"),
                 ("how_heard", "Default answer to 'How did you hear about us?'")]:
        app[k] = ask(q, app.get(k) or "") or None
    app.setdefault("answers", {"_note": "Exact question text -> answer, reused on every application."})
    return app


def ask_app_password() -> str:
    """Hidden input gives no feedback, so confirm the length (a Google app password is 16 letters)."""
    while True:
        pw = getpass.getpass("  App password (hidden, paste ONCE then Enter; Enter alone keeps the current one): ")
        pw = pw.replace(" ", "").strip()
        if not pw:
            return ""
        if len(pw) == 16 and pw.isalpha():
            print("  ✓ received 16 letters")
            return pw
        print(f"  ✗ received {len(pw)} characters; a Google app password is exactly 16 letters"
              + (" (looks like it was pasted more than once)" if len(pw) % 16 == 0 else "") + ". Try again.")


def ask_webhook_url(current: str = "") -> str:
    """The Apps Script web app URL; a stray "y" here once replaced a working URL."""
    while True:
        url = ask("  Web app URL (ends with /exec; Enter keeps the current one)", current)
        if url.startswith("https://script.google.com/") and url.rstrip("/").endswith("/exec"):
            return url.rstrip("/")
        print("  ✗ that isn't a web app URL. Copy it from Deploy > Manage deployments: "
              "https://script.google.com/macros/s/.../exec")


def s_connect() -> None:
    print("\n== Connections (stored only in ~/.job-pilot/.env) ==")
    from .config import load_env
    env = load_env()
    updates = {}
    print("JSearch (Google Jobs incl. LinkedIn/Indeed listings): free key at openwebninja.com/api/jsearch")
    updates["OPENWEBNINJA_API_KEY"] = getpass.getpass("  API key (hidden; Enter to keep/skip): ").strip()
    if ask_yes("Track jobs in a Google Sheet? (see README: Google Sheet)", bool(env.get("SHEET_WEBHOOK_URL"))):
        token = env.get("SHEET_TOKEN") or secrets.token_urlsafe(24)
        print(f"  In Apps Script > Project Settings > Script properties, set TOKEN to:\n    {token}")
        updates["SHEET_TOKEN"] = token
        updates["SHEET_WEBHOOK_URL"] = ask_webhook_url(env.get("SHEET_WEBHOOK_URL", ""))
    if ask_yes("Read job-alert emails from Gmail? (needs a Google app password)", bool(env.get("GMAIL_ADDRESS"))):
        updates["GMAIL_ADDRESS"] = ask("  Gmail address", env.get("GMAIL_ADDRESS", ""))
        updates["GMAIL_APP_PASSWORD"] = ask_app_password()
    write_env(updates)


# --------------------------------------------------------------------- main --

def run(sections: list[str] | None = None) -> int:
    from .config import load_env  # noqa: F401  (validates import)
    home = paths.home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    sections = sections or SECTIONS
    print(f"job-pilot setup: your answers are saved in {home} (outside the project, never committed).")
    defaults = json.loads((paths.REPO / "config.json").read_text())
    user = _load(paths.user_file(), {"name": "", "about": {}, "role": {}, "search": {}})
    skills = _load(paths.skills_file(), {})
    cv = _load(paths.cv_file(), {})
    app = _load(paths.applicant_file(), {})
    catalog = load_catalog()

    for s in sections:
        if s == "about":
            s_about(user)
        elif s == "role":
            s_role(user)
        elif s == "where":
            s_where(user, defaults["region_catalog"])
        elif s == "skills":
            skills = s_skills(paths.skills_file(), catalog, skills)
        elif s == "cv":
            cv = s_cv(cv, user)
        elif s == "answers":
            app = s_answers(app, user, cv)
        elif s == "connect":
            s_connect()
        _save(paths.user_file(), user)
        if cv:
            _save(paths.cv_file(), cv)
        if app:
            _save(paths.applicant_file(), app)

    if user.get("role") and skills and cv.get("experience"):
        paths.facts_file().write_text(build_facts(user, skills, cv))
    missing = doctor(quiet=True)
    print("\nSetup saved." + (f" Still missing: {', '.join(missing)}" if missing else " Everything is set: say 'find jobs'."))
    return 0


def doctor(quiet: bool = False) -> list[str]:
    """What's missing before `find` / `apply` can run fully."""
    import shutil
    from .config import load_env
    env = load_env()
    checks = [
        ("profile (setup)", paths.user_file().exists()),
        ("skills (setup --section skills)", paths.skills_file().exists()),
        ("CV content (setup --section cv)", paths.cv_file().exists()),
        ("form answers (setup --section answers)", paths.applicant_file().exists()),
        ("JSearch key (setup --section connect)", bool(env.get("OPENWEBNINJA_API_KEY") or env.get("RAPIDAPI_KEY"))),
        ("Google Sheet (optional)", bool(env.get("SHEET_WEBHOOK_URL"))),
        ("Gmail alerts (optional)", bool(env.get("GMAIL_APP_PASSWORD"))),
        ("Chrome/Chromium", any(Path(p).exists() for p in ["/Applications/Google Chrome.app"]) or
         bool(shutil.which("google-chrome") or shutil.which("chromium"))),
        ("poppler (pdftotext)", bool(shutil.which("pdftotext"))),
    ]
    missing = [name for name, ok in checks if not ok]
    if not quiet:
        for name, ok in checks:
            print(f"  {'✓' if ok else '✗'} {name}")
    return missing
