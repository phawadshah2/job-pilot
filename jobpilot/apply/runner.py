"""Opens the application in a real Chrome window, fills what it can, attaches
the tailored CV, highlights what's left, and waits. It never clicks Submit."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .rules import Plan, build_plan

HERE = Path(__file__).resolve().parent
DISCOVER_JS = (HERE / "discover.js").read_text()
HIGHLIGHT_JS = (HERE / "highlight.js").read_text()

# Sites where automating the form breaks the ToS: open the page, but don't fill.
OPEN_ONLY = re.compile(r"linkedin\.com|indeed\.|glassdoor\.|bayt\.com|naukrigulf\.com", re.I)
SUBMITTED = re.compile(r"thank(s| you) for (applying|your application)|application (has been )?(submitted|received)|"
                       r"we('ve| have) received your application", re.I)


def site_name(url: str) -> str:
    m = OPEN_ONLY.search(url or "")
    return m.group(0).split(".")[0].capitalize() if m else "This site"


class RestrictedSite(Exception):
    """Raised instead of ever touching a form on a site that forbids automation."""


def ats_of(url: str) -> str:
    for name, rx in (("greenhouse", r"greenhouse\.io|gh_jid="), ("lever", r"jobs\.lever\.co"),
                     ("ashby", r"ashbyhq\.com"), ("workable", r"apply\.workable\.com"),
                     ("smartrecruiters", r"smartrecruiters\.com")):
        if re.search(rx, url, re.I):
            return name
    return "other"


def application_url(url: str, apply_url: str = "") -> str:
    """Jump straight to the form where the ATS has a separate apply page."""
    u = apply_url or url
    ats = ats_of(u)
    if ats == "lever" and not re.search(r"/apply/?$", u):
        return u.rstrip("/") + "/apply"
    if ats == "ashby" and not re.search(r"/application/?$", u):
        return u.rstrip("/") + "/application"
    if ats == "workable" and not re.search(r"/apply/?$", u):
        return u.rstrip("/") + "/apply/"
    return u


@dataclass
class FillResult:
    plan: Plan
    done: list[str]
    failed: list[str]


def discover(page) -> list[dict]:
    fields: list[dict] = []
    for i, fr in enumerate(page.frames):
        try:
            for f in fr.evaluate(DISCOVER_JS):
                f["frame"] = i
                fields.append(f)
        except Exception:
            pass  # cross-origin or detached frame
    return fields


def _has_form(fields: list[dict]) -> bool:
    return any(re.search(r"e-?mail", f"{f['label']} {f['name']} {f['domId']}", re.I) for f in fields)


def open_form(page, url: str) -> list[dict]:
    if OPEN_ONLY.search(url):
        raise RestrictedSite(url)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    if OPEN_ONLY.search(page.url):  # an aggregator redirected us there
        raise RestrictedSite(page.url)
    fields = discover(page)
    if not _has_form(fields):
        # Job page with an Apply button in front of the form.
        for sel in ("a:text-matches('^\\s*apply( for this job| now)?\\s*$', 'i')",
                    "button:text-matches('^\\s*apply( for this job| now)?\\s*$', 'i')",
                    "a:text-matches('^\\s*application\\s*$', 'i')"):
            loc = page.locator(sel).first
            if loc.count():
                loc.click()
                page.wait_for_timeout(2500)
                fields = discover(page)
                if _has_form(fields):
                    break
    return fields


def is_bot_check(page) -> bool:
    try:
        return bool(re.search(r"just a moment|verify you are human|security (check|verification)|captcha",
                              page.title() + " " + page.inner_text("body", timeout=2000)[:2000], re.I))
    except Exception:
        return False


def wait_for_form(context, timeout_s: int = 600, poll_s: float = 2.0):
    """Watch every tab (newest first) until one shows an application form.
    The human does the clicking (and any security check); we only look."""
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for pg in reversed(context.pages):
            if OPEN_ONLY.search(pg.url):
                continue  # never read or fill LinkedIn/Indeed/... even if opened in this window
            try:
                fields = discover(pg)
            except Exception:
                continue
            if _has_form(fields):
                pg.bring_to_front()
                pg.wait_for_timeout(1500)  # let late fields render
                return pg, discover(pg)
        time.sleep(poll_s)
    return None, []


def fill(page, fields: list[dict], applicant: dict, answers: dict[str, str]) -> FillResult:
    if OPEN_ONLY.search(page.url):
        raise RestrictedSite(page.url)
    plan = build_plan(fields, applicant, answers)
    frame_of = {f["id"]: page.frames[f["frame"]] for f in fields}
    done, failed = [], []
    for a in plan.actions:
        loc = frame_of[a.field_id].locator(f'[data-jp-id="{a.field_id}"]')
        try:
            if a.kind == "upload":
                loc.set_input_files(a.value)
            elif a.kind == "fill":
                loc.fill(a.value)
            elif a.kind == "select":
                loc.select_option(label=a.value)
            elif a.kind == "check":
                loc.check(force=True)
            elif a.kind == "combobox":
                _combobox(page, loc, a.value)
            done.append(a.field_id)
        except Exception as e:  # one stubborn field must not abort the rest
            failed.append(f"{a.why}: {str(e).splitlines()[0][:100]}")
    return FillResult(plan, done, failed)


def _combobox(page, loc, value: str) -> None:
    """Type-ahead dropdowns (react-select etc.): type, then pick the matching option."""
    loc.click()
    loc.fill(value)
    page.wait_for_timeout(900)
    opt = page.locator("[role='option']").filter(has_text=re.compile(re.escape(value), re.I)).first
    if opt.count():
        opt.click()
    else:
        loc.fill("")
        raise RuntimeError(f"no dropdown option matching '{value}'")


def highlight(page, fields: list[dict], result: FillResult, title: str) -> None:
    todo_q = {u["question"] for u in result.plan.unanswered}
    from .rules import question_text
    todo = [f["id"] for f in fields if question_text(f) in todo_q]
    for i, fr in enumerate(page.frames):
        try:
            fr.evaluate(HIGHLIGHT_JS, {"ids": {"todo": todo, "done": result.done}, "filled": len(result.done), "title": title})
        except Exception:
            pass


def looks_submitted(page) -> bool:
    try:
        return bool(SUBMITTED.search(page.inner_text("body", timeout=2000)[:20000]))
    except Exception:
        return False


def submitted(page, form_url: str) -> bool:
    """Confirmation text on screen AND the form is gone (or the URL moved on), so text
    like 'application received' sitting next to an unfilled form doesn't count."""
    if OPEN_ONLY.search(page.url or "") or not looks_submitted(page):
        return False
    return page.url != form_url or not _has_form(discover(page))


def write_questions(job_dir: Path, result: FillResult) -> Path | None:
    """questions.md lists what's left; copy a question's exact text into answers.json to auto-fill it next time."""
    qs = result.plan.unanswered
    path = job_dir / "questions.md"
    if not qs:
        path.unlink(missing_ok=True)
        return None
    lines = ["# Questions left on the application form", "",
             "Answer in the browser, or add to `answers.json` in this folder (key = exact question text) "
             "and re-run `apply` to fill them automatically.", ""]
    for q in qs:
        lines.append(f"- **{q['question']}** ({q['type']})")
        if q.get("options"):
            lines.append(f"  - options: {', '.join(q['options'][:15])}{' …' if len(q['options']) > 15 else ''}")
    path.write_text("\n".join(lines) + "\n")
    return path


def load_answers(job_dir: Path, global_answers: dict[str, str]) -> dict[str, str]:
    answers = dict(global_answers)
    p = job_dir / "answers.json"
    if p.exists():
        answers.update(json.loads(p.read_text()))
    return answers
