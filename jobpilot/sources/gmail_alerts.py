"""Job-alert emails read over IMAP, so they cost no LLM tokens.

Needs GMAIL_ADDRESS + GMAIL_APP_PASSWORD in .env (a Google *app password*, not the
account password). Alerts are fetched with BODY.PEEK (not marking them read) and only
marked read once at least one job was parsed from them. Anything else (welcome emails,
verification codes, unknown formats) stays unread and is reported.

LinkedIn and Indeed have text parsers. Every other platform (Bayt, NaukriGulf,
GulfTalent, foundit, Wellfound, Welcome to the Jungle, Arc, ...) goes through one
link-based parser: a job is any link whose real target (click trackers are followed)
matches that platform's job-page pattern; the link's text gives title, company,
location. These links are opened in your normal browser at apply time (never automated).
"""
from __future__ import annotations

import email
import imaplib
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import Message
from html.parser import HTMLParser

from ..models import Job
from ..sites import TRACKERS, classify, host

IMAP_HOST = "imap.gmail.com"


# ------------------------------------------------------------------ parsing --

def parse_indeed(text: str) -> list[Job]:
    """Blocks separated by blank lines: title / 'Company - Location' / [salary] / [Easily apply] /
    snippet... / age / https://<cc>.indeed.com/rc/clk/dl?jk=<id>&..."""
    jobs = []
    for block in re.split(r"\n\s*\n", text):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        url_line = next((l for l in lines if "indeed.com/" in l and "jk=" in l), None)
        if not url_line or len(lines) < 3:
            continue
        m = re.search(r"https?://(\w+)\.indeed\.com/\S*?[?&]jk=([0-9a-f]+)", url_line)
        if not m:
            continue
        title, comp_loc = lines[0], lines[1]
        company, _, location = comp_loc.rpartition(" - ")
        if not company:
            company, location = comp_loc, ""
        body = [l for l in lines[2:] if l != url_line]
        salary = next((l for l in body if re.match(r"^(from\s+)?(AED|SAR|QAR|KWD|BHD|OMR|\$|£|€|A\$|USD|GBP)", l, re.I)), "")
        snippet = " ".join(l for l in body if l != salary and l.lower() != "easily apply"
                           and not re.match(r"^(\d+\+? days? ago|just posted|today|new)$", l, re.I))
        jobs.append(Job(
            source="email:indeed", title=title, company=company.strip(), location=location.strip(),
            url=f"https://{m[1]}.indeed.com/viewjob?jk={m[2]}",
            remote=bool(re.search(r"\bremote\b", location, re.I)) or None,
            description=(f"{salary}. " if salary else "") + snippet, salary=salary,
        ))
    return jobs


def parse_linkedin(text: str) -> list[Job]:
    """Cards separated by '-----' lines: title / company / location / [badges] / 'View job: <url>'."""
    jobs = []
    for chunk in re.split(r"\n-{10,}\n", text):
        lines = [l.strip() for l in chunk.strip().splitlines() if l.strip()]
        lines = [l for l in lines if not re.match(r"^(your job alert for|new jobs match)", l, re.I)]
        view = next((l for l in lines if l.lower().startswith("view job:")), None)
        if not view or len(lines) < 3:
            continue
        m = re.search(r"/jobs/view/(\d+)", view)
        if not m:
            continue
        title, company, location = lines[0], lines[1], lines[2]
        jobs.append(Job(
            source="email:linkedin", title=title, company=company, location=location,
            url=f"https://www.linkedin.com/jobs/view/{m[1]}/",
            remote=bool(re.search(r"\bremote\b", location, re.I)) or None,
        ))
    return jobs


# Job-page URL patterns per platform (matched on host + path of the resolved link).
JOB_PAGES = {
    "Bayt": r"bayt\.com/(en|ar)/[^/]+/jobs/[^/]+-\d+",
    "NaukriGulf": r"naukrigulf\.com/.+-jid-\d+",
    "GulfTalent": r"gulftalent\.com/[^/]+/jobs/[^/]+-\d+",
    "foundit Gulf": r"(founditgulf\.com|foundit\.ae|monstergulf\.com)/(job|jobs|job-listing)/.*\d",
    "Wellfound": r"wellfound\.com/(jobs/\d+|company/[^/]+/jobs/\d+)",
    "Welcome to the Jungle": r"(welcometothejungle\.com/.*jobs?/[^/]+|otta\.com/jobs/[^/]+)",
    "Arc": r"arc\.dev/(remote-jobs/)?(j|details)/[^/]+",
    "Turing": r"turing\.com/(remote-developer-jobs|jobs)/[^/]+",
    "Glassdoor": r"glassdoor\.[a-z.]+/(job-listing/|partner/jobListing)",
    "Dice": r"dice\.com/job-detail/",
    "Reed": r"reed\.co\.uk/jobs/[^/]+/\d+",
    "SEEK": r"seek\.com\.au/job/\d+",
}
_JOB_PAGES = {name: re.compile(r"(?:[a-z0-9-]+\.)*(?:" + rx + ")", re.I) for name, rx in JOB_PAGES.items()}
# Words in link text that are never a title, company or location.
_NOISE = re.compile(r"^(\d+\+?\s*(days?|hours?|weeks?|months?)\s+ago|today|yesterday|just posted|new|easy apply|"
                    r"apply( now)?|view( job)?|see (job|more)|promoted|actively hiring|featured|urgent|hot job)$", re.I)
_AGE = re.compile(r"^(\d+)\+?\s*(day|hour|week|month)s?\s+ago$", re.I)
_NAV = re.compile(r"unsubscribe|privacy|preferences|settings|help|profile|view in (your )?browser|see all|"
                  r"download|log ?in|sign ?in|terms|cv review|courses|premium|referral", re.I)


class _Anchors(HTMLParser):
    """Every <a href> with the separate text pieces inside it (title / company / location...)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, list[str]]] = []
        self._cur: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._cur = (dict(attrs).get("href") or "", [])

    def handle_data(self, data):
        if self._cur is not None and data.strip():
            self._cur[1].append(re.sub(r"\s+", " ", data).strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self.links.append(self._cur)
            self._cur = None


def resolve(url: str, timeout: int = 10) -> str:
    """Final URL behind a click-tracker link (no page body is read)."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (Macintosh) job-pilot"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.geturl()
    except urllib.error.HTTPError as e:  # some trackers refuse HEAD but still redirect
        return e.geturl() or url
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return url


def parse_links(html: str, resolver=resolve, max_resolve: int = 25) -> list[Job]:
    """Jobs from any platform's alert email, recognised by their job-page links."""
    p = _Anchors()
    p.feed(html or "")
    jobs: dict[str, Job] = {}
    resolved = 0
    for href, pieces in p.links:
        if not href.startswith("http") or not pieces or _NAV.search(" ".join(pieces)):
            continue
        url = href
        if TRACKERS.search(host(href)) and resolved < max_resolve:
            url, resolved = resolver(href), resolved + 1
        url = url.split("#")[0]
        bare = re.sub(r"^https?://(www\.)?", "", url).split("?")[0]
        platform = next((n for n, rx in _JOB_PAGES.items() if rx.match(bare)), None)
        if not platform or bare in jobs:
            continue
        age = next((m for m in (_AGE.match(x) for x in pieces) if m), None)
        fields = [x for x in pieces if not _NOISE.match(x)]
        if not fields:
            continue
        title, company, location = (fields + ["", ""])[:3]
        posted = None
        if age:
            n, unit = int(age[1]), age[2].lower()
            posted = datetime.now(timezone.utc) - timedelta(days=n * {"hour": 0, "day": 1, "week": 7, "month": 30}[unit])
        jobs[bare] = Job(
            source=f"email:{platform.lower().replace(' ', '')}", title=title, company=company, location=location,
            url=url.split("?")[0] if classify(url)[0] == "account" else url,
            remote=bool(re.search(r"\bremote\b", f"{title} {location}", re.I)) or None, posted_at=posted,
        )
    return list(jobs.values())


PARSERS = [
    (re.compile(r"jobalert\.indeed\.com", re.I), parse_indeed),
    (re.compile(r"jobalerts-noreply@linkedin\.com", re.I), parse_linkedin),
]


class _LinkText(HTMLParser):
    """HTML -> text that keeps link targets (alerts without a text/plain part)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "tr", "li", "table"):
            self.out.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.out.append(f"\nView job: {href}\n" if "/jobs/view/" in href else f"\n{href}\n")

    def handle_data(self, data):
        self.out.append(data)


def body_text(msg: Message) -> str:
    plain = html = None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    if plain:
        return plain
    if html:
        p = _LinkText()
        p.feed(html)
        return re.sub(r"\n\s*\n\s*\n+", "\n\n", "".join(p.out))
    return ""


def html_part(msg: Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def parse_message(raw: bytes, resolver=resolve) -> tuple[str, list[Job]]:
    """(sender, jobs). LinkedIn/Indeed use their text parsers; everything else the link parser."""
    msg = email.message_from_bytes(raw)
    sender = msg.get("From", "")
    for rx, parser in PARSERS:
        if rx.search(sender):
            return sender, parser(body_text(msg))
    return sender, parse_links(html_part(msg), resolver=resolver)


# --------------------------------------------------------------------- IMAP --

def gmail(cfg: dict) -> list[Job]:
    env = cfg["env"]
    user, pwd = env.get("GMAIL_ADDRESS"), env.get("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        print("    - skipped: set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env (see README)")
        return []
    query = cfg.get("gmail", {}).get("query", "newer_than:7d is:unread")
    jobs: list[Job] = []
    unparsed: dict[str, int] = {}
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        conn.login(user, pwd.replace(" ", ""))
        conn.select("INBOX")
        typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{query}"')
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        done = []
        for uid in uids:
            typ, msg_data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            sender, parsed = parse_message(msg_data[0][1])
            if not parsed:  # welcome mail, verification code, unknown format: left unread
                key = re.sub(r".*@", "", sender).strip("> ")
                unparsed[key] = unparsed.get(key, 0) + 1
                continue
            jobs += parsed
            done.append(uid)
        if done:  # processed alerts are marked read
            conn.uid("STORE", b",".join(done), "+FLAGS", "(\\Seen)")
        print(f"    {len(uids)} unread alert email(s): {len(done)} with jobs, marked read"
              + (f"; left unread (no jobs found): {unparsed}" if unparsed else ""))
    except imaplib.IMAP4.error as e:
        print(f"    ! Gmail login/search failed: {e}. Check GMAIL_ADDRESS / GMAIL_APP_PASSWORD.")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return jobs
