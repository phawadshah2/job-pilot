"""Company career pages hosted on ATS platforms with public job-board APIs.

These are official, unauthenticated endpoints the ATS vendors publish so
companies can embed their job lists. Add company slugs in config.json.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..http import HTTPError, get_json, get_text, post_json
from ..models import Job
from ..text import html_to_text, parse_date


def greenhouse(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for slug in cfg["companies"].get("greenhouse", []):
        data = _safe(lambda: get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"), slug)
        for j in (data or {}).get("jobs", []):
            jobs.append(Job(
                source=f"greenhouse:{slug}",
                title=j.get("title", ""),
                company=j.get("company_name") or slug.title(),
                url=j.get("absolute_url", ""),
                location=(j.get("location") or {}).get("name", ""),
                posted_at=parse_date(j.get("first_published") or j.get("updated_at")),
                description=html_to_text(j.get("content")),
            ))
    return jobs


def lever(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for slug in cfg["companies"].get("lever", []):
        data = _safe(lambda: get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json"), slug)
        for j in data or []:
            cats = j.get("categories") or {}
            wt = (j.get("workplaceType") or "").lower()
            jobs.append(Job(
                source=f"lever:{slug}",
                title=j.get("text", ""),
                company=slug.title(),
                url=j.get("hostedUrl", ""),
                apply_url=j.get("applyUrl", ""),
                location=cats.get("location", "") or "",
                remote=True if wt == "remote" else (False if wt else None),
                posted_at=parse_date(j.get("createdAt")),
                description=j.get("descriptionPlain", "") or html_to_text(j.get("description")),
                employment_type=cats.get("commitment", "") or "",
            ))
    return jobs


def ashby(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for slug in cfg["companies"].get("ashby", []):
        data = _safe(lambda: get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}"), slug)
        for j in (data or {}).get("jobs", []):
            locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
            wt = (j.get("workplaceType") or "").lower()
            jobs.append(Job(
                source=f"ashby:{slug}",
                title=j.get("title", ""),
                company=slug.title(),
                url=j.get("jobUrl", ""),
                apply_url=j.get("applyUrl", ""),
                location=", ".join(l for l in locs if l),
                remote=bool(j.get("isRemote")) or wt == "remote",
                posted_at=parse_date(j.get("publishedAt")),
                description=j.get("descriptionPlain", "") or html_to_text(j.get("descriptionHtml")),
                employment_type=j.get("employmentType", "") or "",
                notes=["hybrid"] if wt == "hybrid" else [],
            ))
    return jobs


def workable(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for slug in cfg["companies"].get("workable", []):
        for j in _workable_all(slug):
            loc = j.get("location") or {}
            wp = (j.get("workplace") or "").lower()
            jobs.append(Job(
                source=f"workable:{slug}",
                title=j.get("title", ""),
                company=slug.title(),
                url=f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/",
                location=", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                remote=bool(j.get("remote")) or wp == "remote",
                posted_at=parse_date(j.get("published")),
                employment_type=j.get("type", "") or "",
                notes=["hybrid"] if wp == "hybrid" else [],
            ))
    return jobs


def _workable_all(slug: str) -> list[dict]:
    """Workable pages 10 at a time; pass nextPage back as `token`."""
    out: list[dict] = []
    body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
    for _ in range(30):
        data = _safe(lambda: post_json(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs", body), slug)
        if not data:
            break
        out += data.get("results", [])
        if not data.get("nextPage"):
            break
        body["token"] = data["nextPage"]
    return out


def smartrecruiters(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for slug in cfg["companies"].get("smartrecruiters", []):
        for term in cfg["search_terms"]:
            data = _safe(lambda: get_json(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&q={term.replace(' ', '%20')}"), slug)
            for j in (data or {}).get("content", []):
                loc = j.get("location") or {}
                jobs.append(Job(
                    source=f"smartrecruiters:{slug}",
                    title=j.get("name", ""),
                    company=(j.get("company") or {}).get("name") or slug.title(),
                    url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                    location=", ".join(x for x in (loc.get("city"), loc.get("country", "").upper()) if x),
                    remote=bool(loc.get("remote")),
                    posted_at=parse_date(j.get("releasedDate")),
                    notes=["hybrid"] if loc.get("hybrid") else [],
                ))
    return jobs


def recruitee(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for slug in cfg["companies"].get("recruitee", []):
        data = _safe(lambda: get_json(f"https://{slug}.recruitee.com/api/offers/"), slug)
        for j in (data or {}).get("offers", []):
            jobs.append(Job(
                source=f"recruitee:{slug}",
                title=j.get("title", ""),
                company=j.get("company_name") or slug.title(),
                url=j.get("careers_url", ""),
                apply_url=j.get("careers_apply_url", ""),
                location=j.get("location", "") or ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                remote=bool(j.get("remote")),
                posted_at=parse_date(j.get("published_at")),
                description=html_to_text((j.get("description") or "") + (j.get("requirements") or "")),
                employment_type=j.get("employment_type_code", "") or "",
                notes=["hybrid"] if j.get("hybrid") else [],
            ))
    return jobs


TT = "{https://teamtailor.com/locations}"


def teamtailor(cfg: dict) -> list[Job]:
    """Teamtailor's public RSS feed (Property Finder, dubizzle, ...)."""
    jobs: list[Job] = []
    for slug in cfg["companies"].get("teamtailor", []):
        xml = _safe(lambda: get_text(f"https://{slug}.teamtailor.com/jobs.rss"), slug)
        if not xml:
            continue
        channel = ET.fromstring(xml).find("channel")
        company = (channel.findtext("title") or slug.title()) if channel is not None else slug.title()
        for it in channel.findall("item") if channel is not None else []:
            locs = [", ".join(x for x in (l.findtext(f"{TT}city"), l.findtext(f"{TT}country")) if x)
                    for l in it.iter(f"{TT}location")]
            status = (it.findtext("remoteStatus") or "").lower()
            jobs.append(Job(
                source=f"teamtailor:{slug}",
                title=it.findtext("title") or "",
                company=it.findtext("company_name") or company,
                url=it.findtext("link") or "",
                location="; ".join(l for l in locs if l),
                remote=True if status in ("fully", "remote") else (False if status in ("none", "onsite") else None),
                posted_at=parse_date(it.findtext("pubDate")),
                description=html_to_text(it.findtext("description")),
                notes=["hybrid"] if status == "hybrid" else [],
            ))
    return jobs


def breezy(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for slug in cfg["companies"].get("breezy", []):
        data = _safe(lambda: get_json(f"https://{slug}.breezy.hr/json"), slug)
        for j in data or []:
            loc = j.get("location") or {}
            jobs.append(Job(
                source=f"breezy:{slug}",
                title=j.get("name", ""),
                company=(j.get("company") or {}).get("name") or slug.title(),
                url=j.get("url", ""),
                location=", ".join(x for x in (loc.get("city"), (loc.get("country") or {}).get("name")) if x),
                remote=bool(loc.get("is_remote")),
                posted_at=parse_date(j.get("published_date")),
                employment_type=(j.get("type") or {}).get("name", ""),
            ))
    return jobs


def _safe(fn, slug: str):
    """A single company renaming its board must not kill the whole run."""
    try:
        return fn()
    except HTTPError as e:
        print(f"    ! {slug}: HTTP {e.code} (slug wrong or board moved?)")
        return None
    except (OSError, ValueError, ET.ParseError) as e:  # timeouts, bad JSON/XML
        print(f"    ! {slug}: {e}")
        return None
