from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""            # raw location text from the source
    remote: bool | None = None    # None = source didn't say
    posted_at: datetime | None = None
    description: str = ""         # plain text, kept verbatim for tailoring later
    employment_type: str = ""
    salary: str = ""
    apply_url: str = ""

    # Filled in by filters.classify()
    work_mode: str = ""           # remote-worldwide | remote-region | onsite-<region> | hybrid-<region>
    region: str = ""              # uae | gcc | uk | us | au | remote (see config filters.regions)
    seniority: str = ""           # senior | unspecified
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Dedup key: same company + same title = same job, regardless of board."""
        return hashlib.sha1(f"{_norm(self.company)}|{_norm_title(self.title)}".encode()).hexdigest()[:16]


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b(inc|llc|ltd|gmbh|fz-?llc|fze|co)\b\.?", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _norm_title(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\((remote|hybrid|onsite|on-site|freelance|contract|full[- ]time)[^)]*\)", "", s)
    s = re.sub(r"\b(sr\.?)\b", "senior", s)
    return re.sub(r"[^a-z0-9]+", "", s)
