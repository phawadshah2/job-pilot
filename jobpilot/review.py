"""Reviewer pass: a second, critical read of a tailored CV + cover letter before the user sees it.

Deterministic checks only (no tokens); Claude fixes what it flags and rebuilds (TAILOR.md).
  fix       must change before sending (a number not in your facts, a JD keyword you have but omitted)
  consider  worth a look (generic opener, cliché, summary too long, repeated phrase)
  gap       JD asks for it and you don't have it: never add it, prepare an honest answer instead
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .text import html_to_text

OPENERS = re.compile(r"\b(i am writing to|to whom it may concern|i am excited to apply|i would like to apply|"
                     r"please find (attached|enclosed)|i am applying for the position of)\b", re.I)
CLICHES = re.compile(r"\b(passionate about|team player|hard[- ]working|go-getter|synergy|think outside the box|"
                     r"results[- ]driven|detail[- ]oriented|self[- ]starter|dynamic individual|rockstar|ninja|"
                     r"guru|best of breed|proven track record)\b", re.I)
# Claims a reader checks: percentages, "20K+", "12+", "5+ years".
NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:%|[kK]\+?|\+)(?=\W|$)|\b\d+\+?\s*(?:years?|yrs)\b")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").lower()


def cover_text(job_dir: Path) -> str:
    """The cover letter as sent: the pasted text version, else the one in tailor.json."""
    if (job_dir / "cover-letter.txt").exists():
        return (job_dir / "cover-letter.txt").read_text()
    from .cv.render import letter_parts
    for name in ("tailor.final.json", "tailor.json"):
        p = job_dir / name
        if p.exists():
            letter = json.loads(p.read_text()).get("cover_letter")
            if letter:
                parts = letter_parts(letter)
                return "\n\n".join(x for x in [parts["greeting"], *parts["paragraphs"], parts["closing"]] if x)
    return ""


def _numbers_not_in_facts(text: str, facts: str, jd: str) -> list[str]:
    ok = _norm(facts) + " " + _norm(jd)
    out = []
    for m in NUMBER.finditer(text):
        claim = _norm(m.group(0)).replace(" ", "")
        core = re.sub(r"\s", "", claim)
        if core not in ok.replace(" ", "") and claim not in out:
            out.append(m.group(0).strip())
    return out


def _repeats(text: str, n: int = 3, min_count: int = 3) -> list[str]:
    words = re.findall(r"[a-z][a-z'-]+", text.lower())
    stop = {"and", "the", "of", "to", "in", "a", "for", "with", "on", "i", "my", "your", "you", "our", "is", "as"}
    grams = Counter(" ".join(words[i:i + n]) for i in range(len(words) - n + 1)
                    if not all(w in stop for w in words[i:i + n]))
    return [g for g, c in grams.most_common(5) if c >= min_count]


def critique(job_dir: Path, facts: str) -> list[tuple[str, str]]:
    meta = json.loads((job_dir / "job.json").read_text())
    jd = (job_dir / "jd.txt").read_text() if (job_dir / "jd.txt").exists() else ""
    cv = html_to_text((job_dir / "cv.html").read_text()) if (job_dir / "cv.html").exists() else ""
    spec = json.loads((job_dir / "tailor.json").read_text()) if (job_dir / "tailor.json").exists() else {}
    letter = cover_text(job_dir)
    out: list[tuple[str, str]] = []

    # --- CV ---------------------------------------------------------------
    cvn = _norm(cv)
    missing = [k for k in meta.get("keywords", [])[1:] if _norm(k) not in cvn]   # [0] is the JD title
    if missing:
        out.append(("fix", f"JD keywords you have but the CV doesn't say: {', '.join(missing)}"))
    for n in _numbers_not_in_facts(cv, facts, jd):
        out.append(("fix", f"CV number not in your facts or the JD: \"{n}\""))
    title_words = {w for w in re.findall(r"[a-z]+", meta.get("title", "").lower()) if len(w) > 3}
    if title_words and not title_words & set(re.findall(r"[a-z]+", spec.get("headline", "").lower())):
        out.append(("consider", f"headline shares no word with the job title \"{meta.get('title')}\""))
    summary_words = len((spec.get("summary") or "").split())
    if summary_words > 110:
        out.append(("consider", f"summary is {summary_words} words; recruiters skim ~60-100"))
    for c in sorted({m.group(0).lower() for m in CLICHES.finditer(spec.get("summary") or "")}):
        out.append(("consider", f"CV summary cliché: \"{c}\""))

    # --- cover letter -----------------------------------------------------
    if letter:
        words = len(letter.split())
        company = re.sub(r"\b(ltd|llc|inc|limited|pvt|private|l\.l\.c)\b\.?", "", meta.get("company", ""), flags=re.I).strip()
        first = company.split()[0].lower() if company else ""
        if words < 150 or words > 330:
            out.append(("consider", f"cover letter is {words} words; aim for 180-300"))
        if first and first not in _norm(letter.split("\n", 1)[0]):
            out.append(("consider", f"greeting doesn't name {company}"))
        if first and _norm(letter).count(first) < 2:
            out.append(("consider", f"letter mentions {company} only once: say why them"))
        for m in sorted({x.group(0).lower() for x in OPENERS.finditer(letter)}):
            out.append(("consider", f"generic opener: \"{m}\""))
        for m in sorted({x.group(0).lower() for x in CLICHES.finditer(letter)}):
            out.append(("consider", f"cliché: \"{m}\""))
        for g in _repeats(letter):
            out.append(("consider", f"repeated phrase: \"{g}\""))
        for n in _numbers_not_in_facts(letter, facts, jd):
            out.append(("fix", f"cover-letter number not in your facts or the JD: \"{n}\""))

    for g in [x.strip() for x in (meta.get("gaps") or "").split(";") if x.strip()]:
        out.append(("gap", f"{g}: not on your CV (prepare an honest answer)"))
    return out


def report(items: list[tuple[str, str]]) -> str:
    if not [i for i in items if i[0] != "gap"]:
        head = "  Reviewer: no issues"
    else:
        n_fix = sum(1 for k, _ in items if k == "fix")
        head = f"  Reviewer: {n_fix} to fix, {sum(1 for k, _ in items if k == 'consider')} to consider"
    return "\n".join([head] + [f"    [{k}] {m}" for k, m in items])
