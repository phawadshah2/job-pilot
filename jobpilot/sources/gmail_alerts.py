"""Job-alert emails (Indeed, LinkedIn) read over IMAP, so they cost no LLM tokens.

Needs GMAIL_ADDRESS + GMAIL_APP_PASSWORD in .env (a Google *app password*, not the
account password). Alerts are fetched with BODY.PEEK (not marking them read), parsed,
and only marked read once parsed. Unknown formats (Bayt, GulfTalent, ...) stay unread
and are reported, so nothing is silently skipped.

LinkedIn / Indeed links are kept as plain job-view URLs; the apply step opens those in
your normal browser (never automated).
"""
from __future__ import annotations

import email
import imaplib
import re
from email.message import Message
from html.parser import HTMLParser

from ..models import Job

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


def parse_message(raw: bytes) -> tuple[str, list[Job] | None]:
    """(sender, jobs) — jobs is None when the sender has no parser yet."""
    msg = email.message_from_bytes(raw)
    sender = msg.get("From", "")
    for rx, parser in PARSERS:
        if rx.search(sender):
            return sender, parser(body_text(msg))
    return sender, None


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
            if parsed is None:
                key = re.sub(r".*@", "", sender).strip("> ")
                unparsed[key] = unparsed.get(key, 0) + 1
                continue
            jobs += parsed
            done.append(uid)
        if done:  # processed alerts are marked read
            conn.uid("STORE", b",".join(done), "+FLAGS", "(\\Seen)")
        print(f"    {len(uids)} unread alert email(s): {len(done)} parsed and marked read"
              + (f"; left unread (no parser yet): {unparsed}" if unparsed else ""))
    except imaplib.IMAP4.error as e:
        print(f"    ! Gmail login/search failed: {e}. Check GMAIL_ADDRESS / GMAIL_APP_PASSWORD.")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return jobs
