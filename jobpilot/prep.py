"""Interview prep pack: Claude writes interview-prep.md in the job folder (INTERVIEW.md);
this renders it to a PDF in the same tidy style as the archived JD."""
from __future__ import annotations

import html
import re
import subprocess
import tempfile
from pathlib import Path

from .cv.jd_pdf import CSS


def _inline(s: str) -> str:
    s = html.escape(s)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)


def md_to_html(md: str) -> str:
    """The small Markdown subset a prep pack uses: #/##/### headings, - and 1. lists, **bold**, paragraphs."""
    out, lst = [], None
    def close():
        nonlocal lst
        if lst:
            out.append(f"</{lst}>")
            lst = None
    for raw in md.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            close()
            continue
        h = re.match(r"^(#{1,3})\s+(.*)", s)
        item = re.match(r"^[-*•]\s+(.*)", s)
        num = re.match(r"^\d+[.)]\s+(.*)", s)
        if h:
            close()
            level = {1: "p class=\"company\"", 2: "h2", 3: "h3"}[len(h[1])]
            tag = level.split()[0]
            out.append(f"<{level}>{_inline(h[2])}</{tag}>")
        elif item or num:
            kind = "ul" if item else "ol"
            if lst != kind:
                close()
                out.append(f"<{kind}>")
                lst = kind
            out.append(f"<li>{_inline((item or num)[1])}</li>")
        else:
            close()
            out.append(f"<p>{_inline(s)}</p>")
    close()
    return "\n".join(out)


def make_pdf(md_path: Path, out_pdf: Path) -> Path:
    doc = (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{html.escape(out_pdf.stem)}</title>"
           f"<style>{CSS} h3 {{ font-size: 10.5pt; margin: 9pt 0 3pt; }} ol {{ margin: 0 0 6pt; padding-left: 18pt; }}"
           f"</style></head><body>{md_to_html(md_path.read_text())}</body></html>")
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
        fh.write(doc)
        tmp = fh.name
    from .tailor import find_chrome
    subprocess.run([find_chrome(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out_pdf}", f"file://{tmp}"], check=True, capture_output=True, timeout=120)
    return out_pdf
