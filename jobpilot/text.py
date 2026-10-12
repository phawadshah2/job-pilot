from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "tr", "section"}


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    if "<" not in s:
        return html.unescape(s).strip()
    p = _Stripper()
    p.feed(html.unescape(s) if "&lt;" in s else s)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n- *\n\s*", "\n- ", text)  # <li><p>x</p></li> -> "- x"
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def parse_date(v) -> datetime | None:
    """Accepts ISO strings, 'YYYY-MM-DD HH:MM:SS', or unix seconds / millis."""
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            n = int(v)
            if n > 10**12:
                n //= 1000
            return datetime.fromtimestamp(n, tz=timezone.utc)
        s = str(v).strip().replace("Z", "+00:00").replace(" ", "T", 1)
        s = re.sub(r"\.(\d{6})\d+", r".\1", s)  # trim >6 fractional digits for py3.9
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None


# --- Gulf location cleanup -------------------------------------------------
# Google Jobs (via JSearch) often returns Arabic place names plus a "• عبر Site"
# ("via Site") suffix, e.g. "الشارقة - إمارة الشارقة • عبر Trabajo.org".

_AR_PLACES = [  # (arabic, english, country) — longest first so multi-word names win
    ("الإمارات العربية المتحدة", "United Arab Emirates", None),
    ("المملكة العربية السعودية", "Saudi Arabia", None),
    ("المدينة المنورة", "Madinah", "Saudi Arabia"),
    ("رأس الخيمة", "Ras Al Khaimah", "United Arab Emirates"),
    ("أبو ظبي", "Abu Dhabi", "United Arab Emirates"),
    ("ابوظبي", "Abu Dhabi", "United Arab Emirates"),
    ("الإمارات", "United Arab Emirates", None),
    ("السعودية", "Saudi Arabia", None),
    ("الشارقة", "Sharjah", "United Arab Emirates"),
    ("الفجيرة", "Fujairah", "United Arab Emirates"),
    ("الرياض", "Riyadh", "Saudi Arabia"),
    ("الدمام", "Dammam", "Saudi Arabia"),
    ("عجمان", "Ajman", "United Arab Emirates"),
    ("العين", "Al Ain", "United Arab Emirates"),
    ("الخبر", "Khobar", "Saudi Arabia"),
    ("دبي", "Dubai", "United Arab Emirates"),
    ("جدة", "Jeddah", "Saudi Arabia"),
    ("مكة", "Makkah", "Saudi Arabia"),
]
_CODES = {"AE": "United Arab Emirates", "SA": "Saudi Arabia"}
_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670]")


def clean_location(loc: str) -> str:
    """'دبي • عبر Indeed, United Arab Emirates' -> 'Dubai, United Arab Emirates'."""
    if not loc:
        return loc
    s = _DIACRITICS.sub("", loc)
    s = re.sub(r"\s*•\s*(?:عبر|via)\s+[^,]*", "", s, flags=re.I)   # "• via <site>"
    s = re.sub(r"\s*-\s*إمارة\s+[^,]*", "", s)                     # "- emirate of X"
    country = None
    for ar, en, c in _AR_PLACES:
        if ar in s:
            s = s.replace(ar, en)
            country = country or c
    parts: list[str] = []
    for part in (x.strip() for x in s.split(",")):
        part = _CODES.get(part, part)
        if part and part.lower() not in (q.lower() for q in parts):
            parts.append(part)
    if country and country not in parts:
        parts.append(country)
    return ", ".join(parts)
