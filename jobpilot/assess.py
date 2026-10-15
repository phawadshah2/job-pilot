"""Helpers for the AI review step of "find jobs".

The rule-based score (scoring.py) only shortlists. Claude then reads each shortlisted JD
and records an assessment (score, match explanation, missing requirements) with
`jobpilot assess`; strong matches are prepared for tailoring.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from .sites import open_only
from .filters import CURRENCY

# Sources whose job lists come straight from the employer's live ATS feed: always current.
LIVE_FEEDS = re.compile(r"^(greenhouse|lever|ashby|workable|smartrecruiters):")
EXPIRED = re.compile(
    r"(no longer (accepting|available|open)|job (has )?(expired|closed)|position (has been )?(filled|closed)"
    r"|this (job|posting|vacancy|role) (is|has been) (closed|removed|filled)|applications? (are )?closed"
    r"|job not found|page not found)", re.I)


def link_status(source: str, url: str, timeout: int = 15) -> bool | None:
    """True = live, False = expired/removed, None = unknown (not checked or blocked).
    Never fetches job platforms you sign in to (LinkedIn, Bayt, ...) or account portals."""
    if not url or open_only(url):
        return None
    if LIVE_FEEDS.match(source or ""):
        return True
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) job-pilot/0.2"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return False if e.code in (404, 410) else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    head = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)[:60_000]
    return False if EXPIRED.search(re.sub(r"<[^>]+>", " ", head)) else True


def extract_salary(description: str) -> str:
    """First salary range or amount mentioned in a JD, as written (e.g. 'AED 12,000 - 15,000 per month')."""
    m = re.search(CURRENCY + r"\s?[\d,.]{3,9}(?:\s*[kK])?(?:\s*(?:-|–|to)\s*(?:" + CURRENCY + r")?\s?[\d,.]{3,9}(?:\s*[kK])?)?"
                  r"(?:\s*(?:per|a|/)\s*(?:month|year|annum|hour))?", description or "", re.I)
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def load_assessments(path: Path) -> list[dict]:
    """[{"key": "...", "score": 0-100, "why": "...", "missing": "..."}] (key prefix allowed)."""
    data = json.loads(path.read_text())
    items = data if isinstance(data, list) else data.get("assessments", [])
    for it in items:
        if not it.get("key") or not isinstance(it.get("score"), (int, float)) or not 0 <= it["score"] <= 100:
            raise ValueError(f"bad assessment (needs key and score 0-100): {it}")
    return items
