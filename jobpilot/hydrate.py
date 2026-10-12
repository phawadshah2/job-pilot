"""Fetch the full JD for sources whose list endpoint omits it (Workable,
SmartRecruiters). Needed before scoring and for the verbatim JD PDF."""
from __future__ import annotations

import re

from .http import HTTPError, URLError, get_json
from .text import html_to_text


def fetch_description(source: str, url: str) -> str:
    try:
        if source.startswith("workable:"):
            m = re.search(r"apply\.workable\.com/([^/]+)/j/([^/]+)", url)
            if m:
                d = get_json(f"https://apply.workable.com/api/v2/accounts/{m[1]}/jobs/{m[2]}")
                return _join([("", d.get("description")), ("Requirements", d.get("requirements")),
                              ("Benefits", d.get("benefits"))])
        if source.startswith("smartrecruiters:"):
            m = re.search(r"jobs\.smartrecruiters\.com/([^/]+)/(\d+)", url)
            if m:
                s = get_json(f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}")["jobAd"]["sections"]
                return _join([(v.get("title", ""), v.get("text")) for v in
                              (s.get(k, {}) for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))])
    except (HTTPError, URLError, KeyError, ValueError) as e:
        print(f"    ! could not fetch JD for {url}: {e}")
    return ""


def _join(sections: list[tuple[str, str | None]]) -> str:
    out = []
    for title, body in sections:
        text = html_to_text(body)
        if text:
            out.append(f"{title}\n{text}" if title else text)
    return "\n\n".join(out)
