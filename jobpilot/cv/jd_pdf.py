#!/usr/bin/env python3
"""Render a saved job description to PDF for the applications archive.

Usage: python3 -m jobpilot.cv.jd_pdf <jd.txt> <Company> <Role> <output.pdf> [applied_date]

The .txt must be the VERBATIM posting. First line may be a URL; it is linked.
"""
import html
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path


CSS = """
@page { size: Letter; margin: 0.6in 0.7in; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: Calibri, Arial, Helvetica, sans-serif; font-size: 10pt;
       line-height: 1.4; color: #111; }
.hdr { border-bottom: 2pt solid #222; padding-bottom: 8pt; margin-bottom: 12pt; }
.company { font-size: 19pt; font-weight: 700; margin: 0 0 2pt; }
.role { font-size: 12.5pt; font-weight: 700; color: #333; margin: 0 0 6pt; }
.meta { font-size: 9pt; color: #555; margin: 0; }
.meta b { color: #222; }
h2 { font-size: 11pt; font-weight: 700; margin: 13pt 0 5pt;
     padding-bottom: 2pt; border-bottom: 0.75pt solid #aaa;
     page-break-after: avoid; break-after: avoid; }
p { margin: 0 0 5pt; }
li { margin: 0 0 3pt; page-break-inside: avoid; break-inside: avoid; }
ul { margin: 0 0 6pt; padding-left: 16pt; }
.note { margin-top: 16pt; padding: 8pt 10pt; background: #f4f4f4;
        border-left: 3pt solid #888; font-size: 9pt; color: #333; }
"""

# Lines matching these become section headings.
HEADINGS = (
    "role description", "role overview", "about the job", "key responsibilities",
    "responsibilities", "requirements", "required skills", "qualifications",
    "preferred", "nice to have", "must have", "what's great in the job",
    "team leadership", "hands-on engineering", "code quality", "ai-assisted development",
    "api, integration", "stakeholder management", "design system",
    "education", "more about the opportunity", "equal opportunity",
)


def render(lines):
    out, in_list = [], False
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        low = stripped.lower().rstrip(":").strip()
        is_head = (
            any(low.startswith(h) for h in HEADINGS)
            and len(stripped) < 70
            and not stripped.startswith(("•", "*", "-"))
        )
        if is_head:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h2>{html.escape(stripped.rstrip(':'))}</h2>")
        elif stripped.startswith(("•", "*", "-")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(stripped.lstrip('•*- ').strip())}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{html.escape(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def make(jd_path, company, role, out_pdf, applied=None):
    """Render a verbatim JD text file to a tidy PDF (kept for interview prep)."""
    applied = applied or date.today().isoformat()

    lines = Path(jd_path).read_text().splitlines()
    source = ""
    if lines and lines[0].strip().startswith("http"):
        source = lines[0].strip()
        lines = lines[1:]

    meta = f"<b>Saved:</b> {applied}"
    if source:
        meta += f" &nbsp;•&nbsp; <b>Source:</b> {html.escape(source)}"

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{html.escape(company)} - {html.escape(role)} - Job Description</title>
<style>{CSS}</style></head><body>
<div class="hdr">
<p class="company">{html.escape(company)}</p>
<p class="role">{html.escape(role)}</p>
<p class="meta">{meta}</p>
</div>
{render(lines)}
<div class="note">Archived job description, kept for interview preparation.
Retained because postings are frequently taken down once a role is filled.</div>
</body></html>"""

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as fh:
        fh.write(doc)
        tmp = fh.name

    Path(out_pdf).parent.mkdir(parents=True, exist_ok=True)
    from ..tailor import find_chrome
    subprocess.run(
        [find_chrome(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={out_pdf}", f"file://{tmp}"],
        check=True, capture_output=True,
    )
    return out_pdf


def main():
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    make(*sys.argv[1:6])
    print(f"wrote {sys.argv[4]}")


if __name__ == "__main__":
    main()
