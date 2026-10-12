"""Per-job CV folders under ~/.job-pilot/jobs/.

prepare  -> <jobs>/<company-slug>/ with jd.txt (verbatim), job.json, BRIEF.md and a
            default cv.html rendered from the user's cv.json.
build    -> tailor.json (written by Claude) -> cv.html, via jobpilot/cv/render.py.
finalize -> refuses CVs that claim forbidden skills or change the design, renders both
            PDFs with headless Chrome, checks the ATS text layer, reports keyword
            coverage default-vs-tailored, and marks the job Ready.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .cv.render import head, render
from .scoring import Lexicon
from .text import html_to_text


class TailorError(Exception):
    pass


def find_chrome() -> str:
    """Google Chrome / Chromium for headless PDF printing (macOS, Linux; or $CHROME)."""
    candidates = [os.environ.get("CHROME", ""),
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    candidates += [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise TailorError("Google Chrome/Chromium not found: install it or set CHROME=/path/to/chrome")


def load_cv() -> dict:
    p = paths.cv_file()
    if not p.exists():
        raise TailorError(f"No CV content at {p}. Run:  python3 -m jobpilot setup --section cv")
    return json.loads(p.read_text())


# ---------------------------------------------------------------- prepare --

def slug(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:max_len].rstrip("-") or "unknown"


def file_label(company: str, role: str) -> str:
    """'<Company> - <Role>' safe for a filename."""
    clean = lambda s: re.sub(r"\s+", " ", re.sub(r"[/\\:*?\"<>|]+", "-", s)).strip(" -.")
    return f"{clean(company)} - {clean(role)}"[:120]


def job_dir_for(row, root: Path | None = None) -> Path:
    root = root or paths.jobs_dir()
    base = root / slug(row["company"])
    for candidate in (base, root / f"{slug(row['company'])}-{slug(row['title'], 30)}"):
        meta = candidate / "job.json"
        if not candidate.exists():
            return candidate
        if meta.exists() and json.loads(meta.read_text()).get("key") == row["key"]:
            return candidate
    return root / f"{slug(row['company'])}-{slug(row['title'], 30)}-{row['key'][:6]}"


def prepare(row, profile: dict, root: Path | None = None) -> Path:
    if not (row["description"] or "").strip():
        raise TailorError("no JD text stored for this job; open the URL and paste it with `jobpilot add`")
    d = job_dir_for(row, root)
    d.mkdir(parents=True, exist_ok=True)

    # jd.txt: first line URL (the JD PDF links it), then the posting verbatim.
    (d / "jd.txt").write_text(f"{row['url']}\n\n{row['description'].strip()}\n")

    have = Lexicon(profile["have"]).phrases(f"{row['title']}\n{row['description']}")
    keywords = [row["title"]] + sorted(set(have.values()), key=str.lower)
    meta = {
        "key": row["key"], "company": row["company"], "title": row["title"], "url": row["url"],
        "apply_url": row["apply_url"] or row["url"], "location": row["location"], "work_mode": row["work_mode"],
        "score": row["score"], "gaps": row["gaps"] or "", "label": file_label(row["company"], row["title"]),
        "keywords": keywords,
    }
    (d / "job.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    if not (d / "cv.html").exists():
        (d / "cv.html").write_text(render(load_cv()))
    (d / "BRIEF.md").write_text(_brief(meta))
    return d


def _brief(meta: dict) -> str:
    kw = "\n".join(f"- {k}" for k in meta["keywords"])
    return f"""# {meta['label']}

- URL: {meta['url']}
- Location: {meta['location']} ({meta['work_mode']})
- Match score: {meta['score']}
- Gaps (do NOT claim; mention them in the report): {meta['gaps'] or 'none detected'}

## JD keywords the candidate genuinely has: use the JD's exact wording where truthful
{kw}

Write tailor.json in this folder (see TAILOR.md), then:

    python3 -m jobpilot build "{meta['key']}"
"""


def build(job_dir: Path) -> Path:
    spec_p = job_dir / "tailor.json"
    if not spec_p.exists():
        raise TailorError(f"no tailor.json in {job_dir}")
    out = job_dir / "cv.html"
    out.write_text(render(load_cv(), json.loads(spec_p.read_text())))
    return out


# --------------------------------------------------------------- finalize --

@dataclass
class FinalizeReport:
    cv_pdf: Path
    jd_pdf: Path
    pages: int
    old_cov: int
    new_cov: int
    table: str
    warnings: list[str]


def _head(html_text: str) -> str:
    """Everything before <body>: fonts + <style>. Tailoring must not touch it."""
    i = html_text.find("<body")
    return re.sub(r"<title>.*?</title>", "", html_text[: i] if i >= 0 else html_text, flags=re.S).strip()


def check_style_unchanged(cv_html: str, reference_html: str | None = None) -> bool:
    return _head(cv_html) == _head(reference_html if reference_html is not None else head())


def check_boundaries(cv_html: str, profile: dict) -> list[str]:
    """Skills the user said they DON'T have must never appear on the CV.
    `allowed_phrases` lets a forbidden word appear inside an approved phrase
    (e.g. 'TypeScript' only within 'TypeScript-comparable')."""
    text = html_to_text(cv_html).lower()
    fb = profile.get("forbidden_in_cv", {})
    hits = []
    allowed = {k.lower(): v.lower() for k, v in fb.get("allowed_phrases", {}).items()}
    for pat in fb.get("patterns", []):
        rx = re.compile(pat if pat.startswith("\\b") else r"(?<![\w-])" + re.escape(pat) + r"(?![\w-])", re.I)
        for m in rx.finditer(text):
            ok = allowed.get(pat.lower())
            if ok and ok in text[max(0, m.start() - 30): m.end() + 30]:
                continue
            hits.append(_context(text, m.start()))
            break
    return hits


def _context(text: str, i: int) -> str:
    return "…" + text[max(0, i - 40): i + 50].replace("\n", " ") + "…"


def finalize(job_dir: Path, profile: dict) -> FinalizeReport:
    from .cv import jd_pdf
    meta = json.loads((job_dir / "job.json").read_text())
    cv_html_path = job_dir / "cv.html"
    cv_html = cv_html_path.read_text()
    cv = load_cv()
    default = render(cv)
    if cv_html == default:
        raise TailorError("cv.html is still the default CV; write tailor.json and run `jobpilot build` first")
    if not check_style_unchanged(cv_html, default):
        raise TailorError("cv.html's <head>/<style> differs from the project design. Tailoring may only change "
                          "text: regenerate it with `jobpilot build`.")

    violations = check_boundaries(cv_html, profile)
    if violations:
        raise TailorError("CV claims skills outside the verified boundaries:\n  " + "\n  ".join(violations))

    label = meta["label"]
    cv_pdf = job_dir / f"{label}.pdf"
    jd_pdf_path = job_dir / f"{label} - JD.pdf"

    # virtual-time-budget lets the Inter web font finish loading before printing.
    _run([find_chrome(), "--headless", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=10000",
          f"--print-to-pdf={cv_pdf}", cv_html_path.resolve().as_uri()], "Chrome (CV)")
    try:
        jd_pdf.make(str(job_dir / "jd.txt"), meta["company"], meta["title"], str(jd_pdf_path))
    except (subprocess.CalledProcessError, OSError) as e:
        raise TailorError(f"JD PDF failed: {e}")

    warnings: list[str] = []
    new_text = _run(["pdftotext", "-layout", str(cv_pdf), "-"], "pdftotext")
    first_name = cv["name"].split()[0].lower()
    if first_name not in new_text.lower() or len(new_text) < 1500:
        warnings.append("CV text layer looks empty/short: ATS may not read it")
    pages = int(re.search(r"Pages:\s+(\d+)", _run(["pdfinfo", str(cv_pdf)], "pdfinfo")).group(1))
    if pages > 2:
        warnings.append(f"CV is {pages} pages; trim to 2")
    fonts = _run(["pdffonts", str(cv_pdf)], "pdffonts")
    if "Inter" not in fonts:
        raise TailorError("the Inter font was not embedded (offline, or Google Fonts unreachable), so the CV "
                          "would not match the design. Re-run finalize when online.")
    # columns: name type encoding emb sub uni "obj gen" -> uni is 3rd from the end
    font_rows = [l.split() for l in fonts.splitlines()[2:] if l.strip()]
    if any(len(t) >= 7 and t[-3] == "no" for t in font_rows):
        warnings.append("a font is not Unicode-mapped (pdffonts uni=no): check copy/paste of the PDF")

    table, old_cov, new_cov = coverage(meta["keywords"], html_to_text(default), new_text)
    (job_dir / "coverage.txt").write_text(table + "\n")
    return FinalizeReport(cv_pdf, jd_pdf_path, pages, old_cov, new_cov, table, warnings)


def coverage(keywords: list[str], old: str, new: str) -> tuple[str, int, int]:
    norm = lambda s: re.sub(r"\s+", " ", s).lower()
    old, new = norm(old), norm(new)
    w = min(48, max(len(k) for k in keywords) + 2)
    lines = [f"{'KEYWORD':<{w}} {'BASE':>5} {'NEW':>5}", "-" * (w + 12)]
    no = nn = 0
    for k in keywords:
        o, n = norm(k) in old, norm(k) in new
        no += o
        nn += n
        flag = "  <-- gained" if n and not o else "  <-- LOST" if o and not n else ""
        lines.append(f"{k[:w-1]:<{w}} {'Y' if o else '.':>5} {'Y' if n else '.':>5}{flag}")
    oc, nc = 100 * no // len(keywords), 100 * nn // len(keywords)
    lines += ["-" * (w + 12), f"{'TOTAL':<{w}} {no:>3}/{len(keywords)} {nn:>3}/{len(keywords)}",
              f"\nBase CV coverage: {oc}%   Tailored coverage: {nc}%"]
    return "\n".join(lines), oc, nc


def _run(cmd: list[str], what: str) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except FileNotFoundError:
        raise TailorError(f"{what}: not installed ({cmd[0]})")
    if p.returncode != 0:
        raise TailorError(f"{what} failed: {p.stderr.strip()[:300]}")
    return p.stdout
