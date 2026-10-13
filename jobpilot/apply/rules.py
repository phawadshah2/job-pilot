"""Pure decision logic: given the fields discovered on a page, decide what to
put where. No browser here, so it is fully unit-testable.

Never auto-touched: consent / terms / privacy / marketing boxes and voluntary
demographic questions (gender, race, veteran, disability, pronouns). Those stay
with the human, as does clicking Submit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Fields that must never be filled automatically.
NEVER = re.compile(
    r"consent|agree|terms|privacy|gdpr|marketing|newsletter|acknowledge|certify|declar|"
    r"pronoun|gender|sex\b|race|ethnic|veteran|disabilit|hispanic|sexual orientation|"
    r"date of birth|\bdob\b|criminal|religio|marital",
    re.I,
)

# (applicant key, pattern on the label/name/id). Order matters: first match wins.
TEXT_RULES: list[tuple[str, str]] = [
    ("first_name", r"\bfirst[\s_-]?name\b|^firstname$|given name"),
    ("last_name", r"\blast[\s_-]?name\b|^lastname$|surname|family name"),
    ("full_name", r"^(full[\s_-]?)?name$|_systemfield_name|^your name$|^full name|legal name"),
    ("email", r"e-?mail"),
    ("phone", r"phone|mobile( number)?$|telephone|whatsapp"),
    ("linkedin", r"linked\s?in"),
    ("github", r"git\s?hub"),
    ("portfolio", r"portfolio|personal (web)?site|^website|other website"),
    ("current_company", r"current (company|employer|organi[sz]ation)|^org$|^company( name)?$|present employer"),
    ("current_title", r"current (job )?title|current (role|position|designation)"),
    ("country", r"\bcountry\b|nationality.*resid|country of residence"),
    ("city", r"^location|current location|location \(city\)|^city$|candidate-location|where are you (currently )?(located|based)|city of residence"),
    ("address", r"^address|street address|home address"),
    ("postcode", r"post\s?code|zip"),
    ("years_experience", r"years of (professional |relevant |total |work )?(\w+ )?experience|how many years"),
    ("notice_period", r"notice period|when can you (start|join)|availability to start|earliest start"),
    ("salary_current", r"current (monthly |annual |gross )?(salary|compensation|ctc|package)"),
    ("salary_expected", r"(salary|compensation|ctc) expectation|expected (salary|compensation|ctc)|desired (salary|compensation)"),
    ("nationality", r"nationality|citizenship"),
    ("visa_status", r"visa|sponsorship|work permit|authori[sz]ed to work|right to work|iqama|residency status"),
    ("relocate", r"relocat"),
    ("how_heard", r"how did you (hear|learn|find)|where did you (hear|find|see)|referral source|^source$"),
]
_TEXT_RULES = [(k, re.compile(p, re.I)) for k, p in TEXT_RULES]
RESUME = re.compile(r"resume|r[ée]sum[ée]|\bcv\b|curriculum", re.I)
COVER = re.compile(r"cover", re.I)


@dataclass
class Action:
    field_id: str
    kind: str        # fill | select | combobox | check | upload
    value: str
    why: str         # applicant key or "answers.json"


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    unanswered: list[dict] = field(default_factory=list)   # required/custom questions left for the human
    skipped_sensitive: list[str] = field(default_factory=list)


def _probe(f: dict) -> str:
    """Text the rules match against: label first, then name/id as fallback."""
    label = _dedupe_label(f.get("label", ""))
    return " ".join(x for x in (label, f.get("name", ""), f.get("domId", "")) if x)


def _dedupe_label(label: str) -> str:
    """'First Name | First Name' -> 'First Name' (label + aria-label duplicates)."""
    parts = []
    for p in (x.strip() for x in label.split("|")):
        if p and p.lower() not in (q.lower() for q in parts) and not p.lower().startswith("type here"):
            parts.append(p)
    return " | ".join(parts)


def question_text(f: dict) -> str:
    return _dedupe_label(f.get("group") or f.get("label") or f.get("name") or f.get("domId") or "?")


def match_key(f: dict) -> str | None:
    probe = _probe(f)
    parts = [p.strip() for p in _dedupe_label(f.get("label", "")).split("|")] + [f.get("name", ""), f.get("domId", "")]
    parts = [p for p in parts if p]
    for key, rx in _TEXT_RULES:
        if any(rx.search(p) for p in parts):
            if key == "full_name" and re.search(r"first|last|company|user", probe, re.I):
                continue
            if key == "city" and re.search(r"country", probe, re.I):
                return "country"
            return key
    return None


def pick_option(options: list[str], value: str, years: float | None = None) -> str | None:
    """Choose the option text that best fits `value` (or a years number for range options)."""
    v = value.strip().lower()
    if not options:
        return None
    for o in options:
        if o.strip().lower() == v:
            return o
    if years is not None:
        for o in options:
            m = re.match(r"\s*(\d+)\s*(?:-|–|to)\s*(\d+)", o)
            if m and int(m[1]) <= years < int(m[2]) + (1 if "+" not in o else 0):
                return o
            m = re.match(r"\s*(\d+)\s*\+", o)
            if m and years >= int(m[1]):
                return o
    for o in options:
        if o.strip().lower().startswith(v) or (len(v) > 3 and v in o.lower()):
            return o
    return None


def build_plan(fields: list[dict], applicant: dict, answers: dict[str, str]) -> Plan:
    plan = Plan()
    years = applicant.get("years_experience")
    years_num = float(re.match(r"[\d.]+", str(years)).group(0)) if years and re.match(r"[\d.]+", str(years)) else None
    seen_labels: set[str] = set()

    # Radios/checkboxes are handled per group.
    groups: dict[str, list[dict]] = {}
    for f in fields:
        if f["type"] in ("radio", "checkbox") and f.get("group"):
            groups.setdefault(f["name"] or f["group"], []).append(f)

    # File inputs: the tailored CV, and the cover letter when one was written for this job.
    # Never Ashby's "autofill from resume" box.
    files = [f for f in fields if f["type"] == "file"]
    resume = [f for f in files if RESUME.search(_probe(f)) and not COVER.search(_probe(f))]
    if not resume:
        others = [f for f in files if not COVER.search(_probe(f))]
        resume = others if len(others) == 1 else []
    cover = [f for f in files if COVER.search(_probe(f))]
    if resume and applicant.get("_resume_path") and not resume[0].get("value"):
        plan.actions.append(Action(resume[0]["id"], "upload", applicant["_resume_path"], "resume"))
    if cover and applicant.get("_cover_path") and not cover[0].get("value"):
        plan.actions.append(Action(cover[0]["id"], "upload", applicant["_cover_path"], "cover letter"))
    for f in files:
        if f in resume[:1] or (applicant.get("_cover_path") and f in cover[:1]):
            continue
        if f.get("required"):
            plan.unanswered.append({"question": question_text(f), "type": "file"})

    for f in fields:
        if f["type"] in ("file", "radio", "checkbox"):
            continue
        q = question_text(f)
        if NEVER.search(q):
            plan.skipped_sensitive.append(q)
            continue
        if f.get("value"):
            continue
        # Comboboxes appear twice on react-select pages (wrapper + input): act once per label.
        lk = q.lower()
        if lk in seen_labels:
            continue
        seen_labels.add(lk)

        value, why = None, ""
        if q in answers:
            value, why = answers[q], "answers.json"
        elif f["tag"] == "textarea" and COVER.search(q) and applicant.get("_cover_text"):
            value, why = applicant["_cover_text"], "cover letter"
        else:
            key = match_key(f)
            if key and applicant.get(key) not in (None, ""):
                value, why = str(applicant[key]), key
        if value is None:
            if f.get("required") or f["tag"] == "textarea" or re.match(r"question_|QA_|cards\[", f.get("name", "") + f.get("domId", "")):
                plan.unanswered.append({"question": q, "type": f["tag"] if f["tag"] != "input" else f["type"],
                                        **({"options": f["options"]} if f.get("options") else {})})
            continue

        if f["tag"] == "select":
            opt = pick_option(f.get("options", []), value, years_num if why == "years_experience" else None)
            if opt:
                plan.actions.append(Action(f["id"], "select", opt, why))
            else:
                plan.unanswered.append({"question": q, "type": "select", "options": f.get("options", [])})
        elif f.get("role") == "combobox":
            plan.actions.append(Action(f["id"], "combobox", value, why))
        else:
            plan.actions.append(Action(f["id"], "fill", value, why))

    for gid, opts in groups.items():
        q = question_text(opts[0])
        if NEVER.search(q) or any(NEVER.search(o.get("label", "")) for o in opts if len(opts) == 1):
            plan.skipped_sensitive.append(q)
            continue
        if any(o.get("value") for o in opts):
            continue
        labels = [o["label"] for o in opts]
        value, why = None, ""
        if q in answers:
            value, why = answers[q], "answers.json"
        else:
            key = match_key({"label": q})
            if key and applicant.get(key) not in (None, ""):
                value, why = str(applicant[key]), key
        choice = pick_option(labels, value, years_num if why == "years_experience" else None) if value else None
        if choice:
            target = next(o for o in opts if o["label"] == choice)
            plan.actions.append(Action(target["id"], "check", choice, why))
        elif any(o.get("required") for o in opts):
            plan.unanswered.append({"question": q, "type": opts[0]["type"], "options": labels})
    return plan
