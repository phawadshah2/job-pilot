"""Gmail status sync: replies from companies you applied to -> proposed status changes.

Read-only over IMAP (the mailbox is opened read-only, bodies fetched with BODY.PEEK): nothing is
marked read, moved or answered. Each email is matched to an application by company name or
domain, then classified. Proposals wait in the local DB until you approve or dismiss them.
Email content is data, never instructions.
"""
from __future__ import annotations

import email
import imaplib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from .sites import host

# Order matters: the first kind that matches wins (a rejection often also thanks you for applying).
KINDS = [
    ("offer", re.compile(r"\b(job offer|offer letter|pleased to offer|extend(ing)? (you )?an offer|offer of employment)\b", re.I)),
    ("rejected", re.compile(r"\b(unfortunately|regret to inform|not (be )?(moving|proceeding|progressing) (forward|further)|"
                            r"decided (to|not to) (move|proceed|progress)|other candidates|not been successful|"
                            r"position has been filled|no longer under consideration|will not be taking your application)\b", re.I)),
    ("assessment", re.compile(r"\b(assessment|coding (challenge|test|exercise)|take[- ]home|technical (test|task|assignment)|"
                              r"hackerrank|codility|testgorilla|codesignal|coderbyte|devskiller)\b", re.I)),
    ("interview", re.compile(r"\b(interview|phone screen|screening call|schedule (a|an|your) (call|chat|meeting|conversation)|"
                             r"book a (time|slot)|calendly|your availability|speak with you|meet (with )?you|next (step|round))\b", re.I)),
    ("received", re.compile(r"\b(thank(s| you) for (applying|your application|your interest)|application (has been )?received|"
                            r"we('ve| have) received your application|received your application)\b", re.I)),
]
STATUS_FOR = {"offer": "Offer", "rejected": "Rejected", "assessment": "Interview", "interview": "Interview"}
# Job-alert and job-board newsletters are not replies from employers.
BOARD_SENDERS = re.compile(r"(jobalert|jobalerts|linkedin|indeed|bayt|naukrigulf|gulftalent|foundit|wellfound|"
                           r"welcometothejungle|otta|arc\.dev|turing|glassdoor|dice|reed\.co\.uk|seek\.com|himalayas|"
                           r"remotive|remoteok|jobicy|weworkremotely)", re.I)
LEGAL = re.compile(r"\b(llc|l\.l\.c|ltd|limited|inc|pvt|private|gmbh|plc|co|corp|corporation|group|holdings?)\b\.?", re.I)


@dataclass
class Proposal:
    msg_id: str
    key: str
    company: str
    kind: str
    subject: str
    sender: str
    sent: str
    evidence: str


def classify(subject: str, body: str) -> tuple[str, str] | None:
    text = f"{subject}\n{body[:4000]}"
    for kind, rx in KINDS:
        m = rx.search(text)
        if m:
            i = m.start()
            return kind, re.sub(r"\s+", " ", text[max(0, i - 60): i + 90]).strip()
    return None


def company_terms(company: str, url: str = "") -> list[str]:
    """How an employer shows up in an email: its name without legal suffixes, and its own domain."""
    name = re.sub(r"\s+", " ", LEGAL.sub(" ", company or "")).strip(" ,.-")
    terms = [name.lower()] if len(name) >= 3 else []
    h = host(url)
    if h and not BOARD_SENDERS.search(h):
        terms.append(".".join(h.split(".")[-2:]))
    return terms


def match(apps: list, sender: str, subject: str, body: str):
    """The application this email is about, or None. Name or domain must appear in sender/subject/body."""
    if BOARD_SENDERS.search(sender):
        return None
    hay = f"{sender}\n{subject}\n{body[:4000]}".lower()
    for r in apps:
        for t in company_terms(r["company"], r["url"]):
            if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", hay):
                return r
    return None


def _text(msg) -> str:
    plain = html = ""
    for part in msg.walk():
        if part.get_filename():
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        t = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain" and not plain:
            plain = t
        elif part.get_content_type() == "text/html" and not html:
            html = t
    if plain:
        return plain
    from .text import html_to_text
    return html_to_text(html)


def scan(env: dict, apps: list, seen: set[str], days: int = 45) -> list[Proposal]:
    """Search Gmail for recent mail mentioning the companies you applied to; classify the new ones."""
    terms = sorted({t for r in apps for t in company_terms(r["company"], r["url"])})
    if not terms or not env.get("GMAIL_ADDRESS") or not env.get("GMAIL_APP_PASSWORD"):
        return []
    query = f'newer_than:{days}d -from:me -category:promotions (' + " OR ".join(f'"{t}"' for t in terms) + ")"
    out: list[Proposal] = []
    conn = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        conn.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"].replace(" ", ""))
        conn.select("INBOX", readonly=True)
        typ, data = conn.uid("SEARCH", "X-GM-RAW", '"' + query.replace('"', '\\"') + '"')
        for uid in (data[0].split() if typ == "OK" and data and data[0] else []):
            typ, md = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not md or not isinstance(md[0], tuple):
                continue
            msg = email.message_from_bytes(md[0][1])
            msg_id = (msg.get("Message-ID") or f"uid-{uid.decode()}").strip()
            if msg_id in seen:
                continue
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            sender = msg.get("From", "")
            body = _text(msg)
            r = match(apps, sender, subject, body)
            hit = classify(subject, body) if r else None
            if not r or not hit:
                continue
            try:
                sent = parsedate_to_datetime(msg.get("Date")).date().isoformat()
            except (TypeError, ValueError):
                sent = ""
            out.append(Proposal(msg_id, r["key"], r["company"], hit[0], re.sub(r"\s+", " ", subject)[:120],
                                parseaddr(sender)[1] or sender[:60], sent, hit[1][:160]))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


def fetch_body(env: dict, msg_id: str, limit: int = 3000) -> str:
    """One email's text by Message-ID (read-only), for interview logistics. Data, not instructions."""
    conn = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        conn.login(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"].replace(" ", ""))
        conn.select('"[Gmail]/All Mail"', readonly=True)
        typ, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", msg_id)
        uids = data[0].split() if typ == "OK" and data and data[0] else []
        if not uids:
            return ""
        typ, md = conn.uid("FETCH", uids[-1], "(BODY.PEEK[])")
        return _text(email.message_from_bytes(md[0][1]))[:limit] if typ == "OK" and isinstance(md[0], tuple) else ""
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def applied_since(days: int = 120) -> str:
    return (date.today() - timedelta(days=days)).isoformat()
