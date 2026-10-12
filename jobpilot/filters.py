"""Cheap, deterministic filtering. Role keywords, regions, languages and deal-breakers
all come from the user's setup (see config.py); nothing here is role-specific.
When unsure, a job is kept and tagged with a note rather than dropped.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import Job


def _rx(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


CURRENCY = r"(AED|SAR|QAR|KWD|BHD|OMR|USD|US\$|\$|GBP|£|EUR|€|AUD|A\$|CAD|C\$|INR|₹|PKR|EGP|SGD|NZD)"


class Filter:
    def __init__(self, cfg: dict) -> None:
        f = cfg["filters"]
        self.role = _rx(f["title_must_match_any"])
        self.keyword = self.role
        self.related = _rx(f["related_titles_need_keyword"]) if f.get("related_titles_need_keyword") else None
        self.exclude = _rx(f["title_exclude_any"])
        self.exclude_unless_keyword = _rx(f["title_exclude_unless_keyword"])
        self.senior = _rx(f["senior_markers"])
        # Ordered target regions (highest priority first). Each: {"points": int, "patterns": [...]}.
        self.regions = [(name, _rx(r["patterns"]), r["points"]) for name, r in f["regions"].items()]
        self.remote_ok = _rx(f["remote_regions_ok"])
        self.worldwide = _rx(f["worldwide_markers"])
        self.max_age = timedelta(days=f["max_age_days"])
        self.drop_unspecified_seniority = f.get("drop_unspecified_seniority", False)
        onsite_only = set(f.get("dealbreakers_onsite_only", []))
        self.dealbreakers = []  # (reason, regex, title_only, onsite_only)
        for reason, pats in f.get("dealbreakers", {}).items():
            for pat in pats:
                title_only = pat.startswith("re:title:")
                body = pat[len("re:title:"):] if title_only else pat[3:] if pat.startswith("re:") else re.escape(pat)
                self.dealbreakers.append((reason, re.compile(body, re.I), title_only, reason in onsite_only))
        self.salary_floor = f.get("salary_floor")  # {"currency": "AED", "per_month": 9000} or None
        bl = cfg.get("blocklist", {})
        self.bl_companies = [x.lower() for x in bl.get("companies", [])]
        self.bl_sources = [x.lower() for x in bl.get("sources", [])]
        self.bl_urls = [re.compile(x, re.I) for x in bl.get("url_patterns", [])]

    # ------------------------------------------------------------ helpers --

    def region_of(self, text: str) -> str | None:
        """First (highest-priority) target region the text mentions."""
        for name, rx, _ in self.regions:
            if rx.search(text or ""):
                return name
        return None

    def dealbreaker(self, title: str, description: str, remote: bool = False) -> str | None:
        for reason, rx, title_only, onsite_only in self.dealbreakers:
            if onsite_only and remote:
                continue
            if rx.search(title) or (not title_only and rx.search(description or "")):
                return reason
        return None

    def low_pay(self, description: str) -> str | None:
        """'AED6,000 - AED7,000 a month' under the user's floor -> note text (same currency only)."""
        floor = self.salary_floor
        if not floor or not floor.get("per_month"):
            return None
        m = re.search(CURRENCY + r"\s?([\d,]{3,9})(?:\.\d+)?\s*(?:-|–|to)\s*(?:" + CURRENCY + r")?\s?([\d,]{3,9})(?:\.\d+)?"
                      r"\s*(?:per|a|/)\s*(month|year|annum)", description or "", re.I)
        if not m:
            return None
        norm = {"US$": "USD", "$": "USD", "£": "GBP", "€": "EUR", "A$": "AUD", "C$": "CAD", "₹": "INR"}
        cur = norm.get(m.group(1).upper(), m.group(1).upper())
        if cur != floor["currency"].upper():
            return None
        top = int(m.group(4).replace(",", "")) / (12 if m.group(5).lower() in ("year", "annum") else 1)
        if top < floor["per_month"]:
            return f"low pay: {cur} {m.group(2)}–{m.group(4)}/{m.group(5).lower()}"
        return None

    def blocked(self, company: str, source: str, url: str) -> bool:
        c, s = (company or "").lower(), (source or "").lower()
        return (any(b in c for b in self.bl_companies) or any(b in s for b in self.bl_sources)
                or any(rx.search(url or "") for rx in self.bl_urls))

    # ------------------------------------------------------------- classify --

    def classify(self, job: Job) -> str | None:
        """Returns a drop reason, or None if the job is kept (and annotated)."""
        title = job.title
        if self.blocked(job.company, job.source, job.url):
            return "blocklisted"

        # --- role -------------------------------------------------------
        if self.exclude.search(title):
            return "excluded title"
        if self.exclude_unless_keyword.search(title) and not self.keyword.search(title):
            return "other cross-platform stack"
        if not self.role.search(title):
            if not (self.related and self.related.search(title)):
                return "not a matching role"
            if job.description and not self.keyword.search(job.description):
                return "related title, role keyword not in JD"
            if not job.description:
                job.notes.append("role keyword not confirmed (no JD in feed)")

        loc = job.location or ""
        loc_and_title = f"{loc} {title}"
        hybrid = "hybrid" in job.notes or re.search(r"\bhybrid\b", loc_and_title, re.I)
        is_remote = bool(job.remote or re.search(r"\bremote\b", loc_and_title, re.I)) and not hybrid

        reason = self.dealbreaker(title, job.description, remote=is_remote)
        if reason:
            return reason

        # --- seniority --------------------------------------------------
        if self.senior.search(title):
            job.seniority = "senior"
        else:
            job.seniority = "unspecified"
            if self.drop_unspecified_seniority:
                return "not senior"

        # --- freshness --------------------------------------------------
        if job.posted_at and datetime.now(timezone.utc) - job.posted_at > self.max_age:
            return "too old"

        # --- location: target regions in the user's priority order ----------
        region = self.region_of(loc_and_title)
        if is_remote:
            # "Remote" / "Remote - Worldwide" => worldwide; "Remote - US" => that region
            rest = re.sub(r"\b(remote|fully|100%|work from home|wfh)\b|[^a-z]", "", loc.lower())
            if not rest or self.worldwide.search(loc):
                job.work_mode, job.region = "remote-worldwide", "remote"
            elif region:
                job.work_mode, job.region = "remote-region", region
            elif self.remote_ok.search(loc):
                job.work_mode, job.region = "remote-region", "remote"
            else:
                return f"remote but restricted ({loc[:40]})"
        elif region:
            job.work_mode, job.region = f"{'hybrid' if hybrid else 'onsite'}-{region}", region
        else:
            return f"onsite outside target ({loc[:40] or 'unknown'})"

        pay = self.low_pay(job.description)
        if pay:
            job.notes.append(pay)
        if "hybrid" in job.notes:
            job.notes.remove("hybrid")
        return None
