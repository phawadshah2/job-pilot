"""Render a CV (US Letter, one design for everyone) from the user's cv.json, optionally
tailored by a job's tailor.json. The design lives in design.css and is never edited
per job; tailoring only changes text, choice and order.

cv.json (written by `jobpilot setup`):
{
  "name": "ALEX DOE", "headline": "Senior Flutter Developer | ...",
  "contact": ["Dubai, UAE", "+971 50 000 0000", "alex@example.com"],
  "links": ["linkedin.com/in/alex", "github.com/alex"],
  "summary": "...",
  "competencies": {"lang": {"label": "Languages", "text": "Dart, Kotlin"}, ...},
  "competency_order": ["lang", ...],
  "experience": [{"id": "acme", "title": "...", "company": "...", "location": "...", "dates": "2022 – Present",
                  "bullets": {"deliver": "...", "perf": "..."}, "order": ["deliver", "perf"]}],
  "projects": [{"id": "shop", "name": "...", "subtitle": "...", "meta": "50K+ users", "tech": "...", "bullets": ["..."]}],
  "education": [{"degree": "...", "dates": "2015 – 2019", "school": "..."}]
}

tailor.json (written per job by Claude):
{
  "headline": "...", "summary": "...",
  "skills": ["lang", {"key": "apis", "text": "..."}, {"label": "Device Features", "text": "..."}],
  "experience": {"acme": ["deliver", "a reworded bullet as plain text", "perf"]},
  "project_order": ["shop", "..."]
}
Ids insert the user's own wording from cv.json; other strings are used as written. Plain text
only: everything is escaped here.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

DESIGN = Path(__file__).resolve().parent / "design.css"
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
         '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=block" rel="stylesheet">')


class SpecError(Exception):
    pass


def _e(s: str) -> str:
    return html.escape(str(s), quote=False)


def head(title: str = "CV") -> str:
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>{_e(title)}</title>\n'
            f"{FONTS}\n<style>\n{DESIGN.read_text()}</style>\n</head>\n")


def _is_id(s) -> bool:
    return isinstance(s, str) and bool(re.fullmatch(r"[a-z0-9_]{2,24}", s))


def _skills(cv: dict, items) -> list[tuple[str, str]]:
    pool = cv.get("competencies", {})
    out = []
    for it in items:
        if isinstance(it, str):
            if it not in pool:
                raise SpecError(f"skills: unknown id '{it}' (known: {', '.join(pool)})")
            out.append((pool[it]["label"], pool[it]["text"]))
        else:
            base = pool.get(it.get("key", ""), {})
            label, text = it.get("label") or base.get("label"), it.get("text") or base.get("text")
            if not label or not text:
                raise SpecError(f"skills: entry needs label+text or a known key: {it}")
            out.append((label, text))
    return out


def _bullets(job: dict, items) -> list[str]:
    pool = job.get("bullets", {})
    out = []
    for it in items:
        if it in pool:
            out.append(pool[it])
        elif _is_id(it):
            raise SpecError(f"experience '{job['id']}': unknown bullet id '{it}' (known: {', '.join(pool)})")
        else:
            out.append(it)
    return out


def render(cv: dict, spec: dict | None = None) -> str:
    spec = spec or {}
    for k in ("headline", "summary"):
        if k in spec and not spec[k]:
            raise SpecError(f"tailor.json '{k}' is empty")
    exp_spec = spec.get("experience", {})
    unknown = set(exp_spec) - {j["id"] for j in cv.get("experience", [])}
    if unknown:
        raise SpecError(f"experience: unknown job id(s) {sorted(unknown)}")

    sep = '<span class="sep">|</span>'
    b = [head(f"{cv['name'].title()} — {spec.get('headline', cv.get('headline', ''))}".split(" | ")[0]), "<body>\n"]
    b.append(f'<p class="name">{_e(cv["name"])}</p>')
    b.append(f'<p class="headline">{_e(spec.get("headline") or cv.get("headline", ""))}</p>')
    for line in (cv.get("contact"), cv.get("links")):
        if line:
            b.append('<p class="contact">' + sep.join(_e(x) for x in line) + "</p>")

    b.append("\n<h2>PROFESSIONAL SUMMARY</h2>")
    b.append(f'<p class="summary">{_e(spec.get("summary") or cv.get("summary", ""))}</p>')

    skills = _skills(cv, spec.get("skills") or cv.get("competency_order") or list(cv.get("competencies", {})))
    if skills:
        b.append("\n<h2>CORE COMPETENCIES</h2>")
        b += [f'<p class="skill"><b>{_e(l)}:</b> {_e(t)}</p>' for l, t in skills]

    if cv.get("experience"):
        b.append("\n<h2>PROFESSIONAL EXPERIENCE</h2>")
        for job in cv["experience"]:
            items = _bullets(job, exp_spec.get(job["id"]) or job.get("order") or list(job.get("bullets", {})))
            where = " — ".join(x for x in (job.get("company"), job.get("location")) if x)
            b.append(f'\n<div class="job"><span><span class="title">{_e(job["title"])}</span>{sep}{_e(where)}</span>'
                     f'<span class="dates">{_e(job.get("dates", ""))}</span></div>')
            b.append('<ul class="exp">\n' + "\n".join(f"<li>{_e(t)}</li>" for t in items) + "\n</ul>")

    projects = cv.get("projects", [])
    if spec.get("project_order"):
        by = {p.get("id"): p for p in projects} | {p["name"]: p for p in projects}
        missing = [x for x in spec["project_order"] if x not in by]
        if missing:
            raise SpecError(f"project_order: unknown {missing} (known: {[p.get('id') or p['name'] for p in projects]})")
        projects = [by[x] for x in spec["project_order"]]
    if projects:
        b.append("\n<h2>KEY PROJECTS</h2>")
        for p in projects:
            title = " — ".join(x for x in (p["name"], p.get("subtitle")) if x)
            meta = f'<span class="meta">{sep}{_e(p["meta"])}</span>' if p.get("meta") else ""
            b.append(f'\n<p class="proj">{_e(title)}{meta}</p>')
            if p.get("tech"):
                b.append(f'<p class="tech"><b>Tech:</b> {_e(p["tech"])}</p>')
            if p.get("bullets"):
                b.append('<ul class="proj-points">\n' + "\n".join(f"<li>{_e(t)}</li>" for t in p["bullets"]) + "\n</ul>")

    if cv.get("education"):
        b.append("\n<h2>EDUCATION</h2>")
        for ed in cv["education"]:
            b.append(f'<div class="edu"><span class="degree">{_e(ed["degree"])}</span><span class="dates">{_e(ed.get("dates", ""))}</span></div>')
            if ed.get("school"):
                b.append(f'<p class="school">{_e(ed["school"])}</p>')

    b.append("\n</body>\n</html>\n")
    return "\n".join(b)
