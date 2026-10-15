"""Remote-first job boards with free, public, ToS-friendly APIs."""
from __future__ import annotations

import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from ..http import get_json, get_text
from ..models import Job
from ..text import html_to_text, parse_date


def remotive(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for term in cfg["search_terms"]:
        data = get_json("https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode({"search": term}))
        for j in data.get("jobs", []):
            jobs.append(Job(
                source="remotive",
                title=j.get("title", ""),
                company=j.get("company_name", ""),
                url=j.get("url", ""),
                location=j.get("candidate_required_location", "") or "",
                remote=True,
                posted_at=parse_date(j.get("publication_date")),
                description=html_to_text(j.get("description")),
                employment_type=j.get("job_type", ""),
                salary=j.get("salary", "") or "",
            ))
    return jobs


def remoteok(cfg: dict) -> list[Job]:
    # RemoteOK asks API users to link back to them; the URL we store does that.
    tag = urllib.parse.quote(cfg["search_terms"][0].lower().replace(" ", "-"))
    data = get_json(f"https://remoteok.com/api?tag={tag}")
    jobs: list[Job] = []
    for j in data[1:] if isinstance(data, list) else []:
        sal = ""
        if j.get("salary_min"):
            sal = f"${j['salary_min']}-{j.get('salary_max') or ''}"
        jobs.append(Job(
            source="remoteok",
            title=j.get("position", ""),
            company=j.get("company", ""),
            url=j.get("url", ""),
            apply_url=j.get("apply_url", ""),
            location=j.get("location", "") or "",
            remote=True,
            posted_at=parse_date(j.get("epoch") or j.get("date")),
            description=html_to_text(j.get("description")),
            salary=sal,
        ))
    return jobs


def jobicy(cfg: dict) -> list[Job]:
    tag = urllib.parse.quote(cfg["search_terms"][0].lower())
    data = get_json(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={tag}")
    jobs: list[Job] = []
    for j in data.get("jobs", []):
        jobs.append(Job(
            source="jobicy",
            title=j.get("jobTitle", ""),
            company=j.get("companyName", ""),
            url=j.get("url", ""),
            location=j.get("jobGeo", "") or "",
            remote=True,
            posted_at=parse_date(j.get("pubDate")),
            description=html_to_text(j.get("jobDescription")),
            employment_type=", ".join(j.get("jobType") or []),
        ))
    return jobs


def himalayas(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for term in cfg["search_terms"]:
        # Search is relevance-ranked and paged with ?page=N (offset is ignored);
        # past ~page 5 results stop being about the keyword.
        for page in range(1, 6):
            data = get_json("https://himalayas.app/jobs/api/search?" + urllib.parse.urlencode({"q": term, "page": page}))
            batch = data.get("jobs", [])
            for j in batch:
                restr = j.get("locationRestrictions") or []
                jobs.append(Job(
                    source="himalayas",
                    title=j.get("title", ""),
                    company=j.get("companyName", ""),
                    url=j.get("guid") or j.get("applicationLink", ""),
                    apply_url=j.get("applicationLink", ""),
                    # Empty restrictions on Himalayas means "hire from anywhere".
                    location=", ".join(restr) if restr else "Worldwide",
                    remote=True,
                    posted_at=parse_date(j.get("pubDate")),
                    description=html_to_text(j.get("description")),
                    employment_type=j.get("employmentType", "") or "",
                ))
            if not batch:
                break
            time.sleep(0.4)
    return jobs


WWR_FEEDS = ("https://weworkremotely.com/remote-jobs.rss",
             "https://weworkremotely.com/categories/remote-programming-jobs.rss")


def weworkremotely(cfg: dict) -> list[Job]:
    """We Work Remotely's public RSS feeds. Titles read 'Company: Role'."""
    seen: dict[str, Job] = {}
    for feed in WWR_FEEDS:
        channel = ET.fromstring(get_text(feed)).find("channel")
        for it in channel.findall("item") if channel is not None else []:
            link = it.findtext("link") or ""
            company, _, title = (it.findtext("title") or "").partition(": ")
            if not title:
                company, title = "", company
            region = it.findtext("region") or ""
            seen[link] = Job(
                source="weworkremotely",
                title=title.strip(),
                company=company.strip(),
                url=link,
                location="Worldwide" if re.search(r"anywhere", region, re.I) else region,
                remote=True,
                posted_at=parse_date(it.findtext("pubDate")),
                description=html_to_text(it.findtext("description")),
            )
    return list(seen.values())
