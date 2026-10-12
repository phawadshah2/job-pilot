"""Deterministic job/profile match score (0-100). No LLM: it only has to *rank*
the day's jobs and surface gaps; the judgement-heavy tailoring happens later.

Breakdown (max points):
  role_focus     25  a role keyword in the title, or how central it is in the JD
  skills         30  share of the JD's recognised skills you have
  seniority      15  senior title vs unspecified
  experience     15  JD's years requirement vs your years
  location       15  the job's region, by the user's priority order (config.region_points)
  gaps          -30  penalty per hard gap (halved when it's a "nice to have")
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

NICE_TO_HAVE = re.compile(r"\b(nice[- ]to[- ]have|bonus|a plus|is a plus|preferred|advantage|desirable|ideally|familiarity)\b", re.I)


def _compile(pattern: str) -> re.Pattern:
    if pattern.startswith("cs:"):
        return re.compile(pattern[3:])
    if pattern.startswith("re:"):
        return re.compile(pattern[3:], re.I)
    return re.compile(r"(?<![\w-])" + re.escape(pattern) + r"(?![\w-])", re.I)


class Lexicon:
    def __init__(self, groups: dict[str, list[str]]) -> None:
        self.items = {name: [_compile(p) for p in pats] for name, pats in groups.items() if not name.startswith("_")}

    def find(self, text: str) -> dict[str, list[str]]:
        """skill name -> the lines of `text` that mention it."""
        lines = [l for l in re.split(r"[\n\r]+|(?<=[.;])\s+", text) if l.strip()]
        hits: dict[str, list[str]] = {}
        for name, rxs in self.items.items():
            for line in lines:
                if any(rx.search(line) for rx in rxs):
                    hits.setdefault(name, []).append(line)
        return hits

    def phrases(self, text: str) -> dict[str, str]:
        """skill name -> the exact wording `text` used (first occurrence)."""
        out: dict[str, str] = {}
        for name, rxs in self.items.items():
            for rx in rxs:
                m = rx.search(text)
                if m:
                    out[name] = m.group(0).strip()
                    break
        return out


@dataclass
class Score:
    total: int
    breakdown: dict[str, int]
    matched: list[str]
    gaps: list[str]               # "React Native" or "React Native (nice-to-have)"
    unverified: list[str]
    years_required: int | None
    notes: list[str] = field(default_factory=list)

    def gaps_text(self) -> str:
        parts = self.gaps + [f"{u}?" for u in self.unverified]
        if self.years_required:
            parts.append(f"{self.years_required}+ yrs asked")
        return ", ".join(parts)


class Scorer:
    def __init__(self, profile: dict, today: date | None = None, region_points: dict | None = None,
                 keywords: list[str] | None = None) -> None:
        self.region_points = dict(region_points or {})
        self.keyword = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b", re.I) if keywords \
            else re.compile(r"(?!x)x")  # no role keywords -> role_focus from skills only
        self.have = Lexicon(profile["have"])
        self.gap = Lexicon(profile["gap"])
        self.unverified = Lexicon(profile["unverified"])
        y, m = map(int, profile["career_start"].split("-"))
        today = today or date.today()
        self.my_years = (today.year - y) + (today.month - m) / 12

    @classmethod
    def from_file(cls, path: Path) -> "Scorer":
        return cls(json.loads(path.read_text()))

    def score(self, title: str, description: str, work_mode: str, seniority: str, region: str | None = None) -> Score:
        jd = description or ""
        b: dict[str, int] = {}

        # Role focus (the user's role keywords)
        n_kw = len(self.keyword.findall(jd))
        if self.keyword.search(title):
            b["role_focus"] = 25
        else:
            b["role_focus"] = 18 if n_kw >= 3 else 12 if n_kw >= 1 else 0

        # Skills
        have = self.have.find(f"{title}\n{jd}")
        gap = self.gap.find(jd)
        unv = self.unverified.find(jd)
        hard_gaps = {g: lines for g, lines in gap.items() if not all(NICE_TO_HAVE.search(l) for l in lines)}
        soft_gaps = {g for g in gap if g not in hard_gaps}
        recognised = len(have) + len(gap)
        b["skills"] = round(30 * len(have) / recognised) if recognised else 15
        b["gaps"] = -min(30, 10 * len(hard_gaps) + 4 * len(soft_gaps))

        # Seniority
        b["seniority"] = 15 if seniority == "senior" else 9

        # Experience
        years = _years_required(jd)
        if years is None:
            b["experience"] = 11
        elif years <= self.my_years + 0.5:
            b["experience"] = 15
        elif years <= self.my_years + 2:
            b["experience"] = 7
        else:
            b["experience"] = 0

        # Location: points by the user's region priority; remote-region slightly below worldwide
        region = region or _legacy_region(work_mode)
        pts = self.region_points.get(region, 8)
        if work_mode == "remote-region" and region == "remote":
            pts -= 1
        b["location"] = pts

        notes = []
        if not jd.strip():
            notes.append("no JD text: score is a guess")
        if region == "gcc" and not work_mode.startswith("remote"):
            notes.append("GCC: relocation")
        elif region in ("uk", "us", "au") and not work_mode.startswith("remote"):
            notes.append(f"{region.upper()}: relocation + work rights")

        return Score(
            total=max(0, min(100, sum(b.values()))),
            breakdown=b,
            matched=sorted(have),
            gaps=sorted(hard_gaps) + [f"{g} (nice-to-have)" for g in sorted(soft_gaps)],
            unverified=sorted(unv),
            years_required=years if years and years > self.my_years + 0.5 else None,
            notes=notes,
        )


def _legacy_region(work_mode: str) -> str:
    """Rows stored before regions existed (onsite-gulf / hybrid-gulf / remote-*)."""
    if work_mode.startswith("remote"):
        return "remote"
    if work_mode.endswith("gulf"):
        return "uae"
    return work_mode.split("-", 1)[-1] if "-" in work_mode else ""


_YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?(?:\+\s*)?years?(?:'|’)?\s*(?:of\s+)?"
    r"(?:[\w/&+.,-]+\s+){0,6}?(?:experience|exp\b|in\s+(?:mobile|software|ios|android|app|web|backend|frontend|data|\w+\s+development))",
    re.I,
)


def _years_required(jd: str) -> int | None:
    """Smallest 'N years experience' figure that looks like the main requirement."""
    found = [int(m.group(1)) for m in _YEARS.finditer(jd)]
    found = [n for n in found if 1 <= n <= 20]
    return min(found) if found else None
