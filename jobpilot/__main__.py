"""job-pilot CLI.

Daily
    python3 -m jobpilot fetch              # fetch, filter, store, score, push to the sheet
    python3 -m jobpilot top                # ranked list of untailored jobs
    python3 -m jobpilot prepare --top 10   # set up ~/.job-pilot/jobs/<company>/ for the best matches
    (Claude tailors each cv.html per TAILOR.md)
    python3 -m jobpilot finalize <key>     # guard, render both PDFs, verify, mark Ready
    .venv/bin/python -m jobpilot apply     # pre-fill each Ready job in Chrome; YOU click Submit
    python3 -m jobpilot status <key> Submitted

Other
    python3 -m jobpilot fetch --dry-run --why
    python3 -m jobpilot add --url URL --company X --title Y --jd jd.txt   # a job you found yourself
    python3 -m jobpilot score [--rescore]
    python3 -m jobpilot sync               # retry pushing rows that haven't reached the sheet
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import config, paths, sheet
from . import status as status_mod
from .filters import Filter
from .http import HTTPError, URLError
from .http import get_json
from .hydrate import fetch_description
from .models import Job
from .scoring import Scorer
from .sources import SOURCES
from .store import Store
from .tailor import TailorError, finalize, prepare

STATUSES = status_mod.STATUSES


def load_cfg() -> dict:
    """Generic defaults + ~/.job-pilot/user.json + ~/.job-pilot/.env (see config.py)."""
    return config.load()


def load_profile() -> dict:
    return config.load_skills()


def make_scorer(cfg: dict | None = None) -> Scorer:
    cfg = cfg or load_cfg()
    return Scorer(load_profile(), region_points=cfg["region_points"], keywords=cfg["filters"]["title_must_match_any"])


def open_store() -> Store:
    return Store(paths.data_dir() / "jobs.db")


# ------------------------------------------------------------------ fetch --

def cmd_fetch(args) -> int:
    cfg = load_cfg()
    flt = Filter(cfg)
    store = open_store()
    cfg["_usage"] = store
    enabled = [s for s in cfg["sources"] if cfg["sources"][s]]
    if args.only:
        enabled = [s for s in enabled if s in args.only.split(",")]

    kept, dropped, seen_this_run = [], Counter(), set()
    for name in enabled:
        print(f"→ {name}")
        try:
            raw = SOURCES[name](cfg)
        except (HTTPError, URLError, TimeoutError, ValueError) as e:
            print(f"    ! failed: {e}")
            continue
        n_new = 0
        for job in raw:
            if not job.title or not job.url:
                continue
            reason = flt.classify(job)
            if reason:
                dropped[reason.split(" (")[0]] += 1
                if args.why and flt.role.search(job.title):
                    print(f"    · drop [{reason}] {job.title} @ {job.company}")
                continue
            if job.key in seen_this_run or store.is_known(job.key):
                dropped["duplicate / already seen"] += 1
                continue
            seen_this_run.add(job.key)
            kept.append(job)
            n_new += 1
        print(f"    {len(raw)} fetched, {n_new} new & relevant")

    print(f"\n=== {len(kept)} new relevant jobs ===")
    for j in ([] if getattr(args, "quiet", False) else sorted(kept, key=lambda j: (j.work_mode, j.seniority != "senior"))):
        note = f"  [{'; '.join(j.notes)}]" if j.notes else ""
        print(f"  {j.work_mode:<16} {j.seniority:<11} {j.title[:55]:<55} @ {j.company[:28]:<28} {j.location[:30]}{note}")
        print(f"  {'':<28} {j.url}")
    if dropped:
        print("\nDropped: " + ", ".join(f"{k}={v}" for k, v in dropped.most_common()))

    if args.dry_run:
        print("\n(dry run: nothing saved)")
        return 0
    for j in kept:
        store.add(j)
    store.commit()
    if getattr(args, "no_post", False):
        return 0
    score_pending(store, make_scorer(cfg))
    print_top(store, 10)
    return sync(cfg, store)


def cmd_clean(args) -> int:
    """Re-apply the blocklist and the aggregator rule to stored jobs not acted on yet."""
    from .sites import is_aggregator
    store, flt = open_store(), Filter(load_cfg())
    n = 0
    for r in store.query("status IN ('New', 'Tailoring', 'Ready', 'Approved')"):
        why = ("blocklisted" if flt.blocked(r["company"], r["source"], r["url"])
               else "aggregator link" if is_aggregator(r["apply_url"] or r["url"]) else "")
        if why:
            notes = "; ".join(x for x in [r["notes"], why] if x)
            store.update(r["key"], status="Skipped", notes=notes)
            n += 1
            print(f"  skipped  {r['key'][:8]}  {r['title'][:50]} @ {r['company']}")
    store.commit()
    print(f"{n} job(s) skipped.")
    return sync(load_cfg(), store)


# ------------------------------------------------------------------ score --

def score_pending(store: Store, scorer: Scorer, rescore: bool = False) -> int:
    rows = store.query("1=1" if rescore else "score IS NULL")
    for r in rows:
        desc = r["description"] or ""
        if not desc.strip():
            desc = fetch_description(r["source"], r["url"])
            if desc:
                store.update(r["key"], description=desc)
        region = r["region"] or _region_guess(r)
        if region and not r["region"]:
            store.update(r["key"], region=region)
        s = scorer.score(r["title"], desc, r["work_mode"], r["seniority"], region=region)
        notes = [n for n in (r["notes"] or "").split("; ") if n and n not in s.notes] + s.notes
        store.update(r["key"], score=s.total, score_breakdown=json.dumps(s.breakdown),
                     matched=", ".join(s.matched), gaps=s.gaps_text(), notes="; ".join(notes))
    store.commit()
    if rows:
        print(f"\nScored {len(rows)} job(s).")
    return len(rows)


_FILTER = None


def _region_guess(r) -> str | None:
    """Rows stored before regions existed."""
    global _FILTER
    if (r["work_mode"] or "").startswith("remote"):
        return "remote" if r["work_mode"] == "remote-worldwide" else None
    _FILTER = _FILTER or Filter(load_cfg())
    return _FILTER.region_of(f"{r['location']} {r['title']}")


def cmd_score(args) -> int:
    store = open_store()
    score_pending(store, make_scorer(), rescore=args.rescore)
    print_top(store, 20)
    return sync(load_cfg(), store)


def print_top(store: Store, n: int, min_score: int = 0) -> list:
    rows = by_priority(store.query("status='New' AND score IS NOT NULL AND score >= ?", (min_score,), order="score DESC, first_seen DESC"))[:n]
    if not rows:
        print("\nNo untailored jobs waiting.")
        return []
    print(f"\n=== Top {len(rows)} untailored ===")
    for r in rows:
        print(f"  {r['score']:>3}  {r['key'][:8]}  {r['work_mode']:<16} {r['title'][:48]:<48} @ {r['company'][:24]}")
        if r["gaps"]:
            print(f"  {'':<15}gaps: {r['gaps']}")
    return rows


def by_priority(rows: list, order: list | None = None) -> list:
    """The user's region priority (from setup) first, then best score within a region."""
    if order is None:
        order = load_cfg()["region_order"]
    return sorted(rows, key=lambda r: (order.index(r["region"]) if r["region"] in order else 99, -(r["score"] or 0)))


def cmd_top(args) -> int:
    print_top(open_store(), args.n, args.min)
    return 0


# ---------------------------------------------------------------- tailor --

def resolve(store: Store, ref: str):
    """Accepts a full key, a key prefix (>=4 chars), or a job folder."""
    p = Path(ref).expanduser()
    if (p / "job.json").exists():
        ref = json.loads((p / "job.json").read_text())["key"]
    rows = store.query("key LIKE ?", (ref + "%",)) if len(ref) >= 4 else []
    if len(rows) != 1:
        raise SystemExit(f"'{ref}': {'no job' if not rows else 'ambiguous'} (use a longer key prefix)")
    return rows[0]


def cmd_prepare(args) -> int:
    store, profile = open_store(), load_profile()
    if args.keys:
        rows = [resolve(store, k) for k in args.keys]
    else:
        rows = by_priority(store.query("status='New' AND score >= ?", (args.min,), order="score DESC, first_seen DESC"))[:args.top]
    if not rows:
        print(f"Nothing to prepare (no New jobs scoring >= {args.min}).")
        return 0
    for r in rows:
        try:
            d = prepare(r, profile)
        except TailorError as e:
            print(f"  ! {r['company']} - {r['title']}: {e}")
            continue
        prior = previous_applications(store, r, d)
        notes = r["notes"] or ""
        if prior and "applied before" not in notes:
            notes = "; ".join(x for x in [notes, "applied before: " + ", ".join(prior)] if x)
        store.update(r["key"], status=_forward(r["status"], "Tailoring"), cv_dir=str(d), notes=notes)
        print(f"  {r['score']:>3}  {r['key'][:8]}  {d}")
        if prior:
            print(f"       ⚠ same company before: {', '.join(prior)}")
    store.commit()
    print("\nNext: tailor each cv.html (TAILOR.md), then `python3 -m jobpilot finalize <key>`.")
    return sync(load_cfg(), store)


def previous_applications(store: Store, r, this_dir: Path) -> list[str]:
    """Earlier CV folders for the same company (incl. ones made before job-pilot, listed in
    user.json "legacy_cv_dirs") and other tracked jobs there that got past Ready."""
    import re
    from . import tailor
    core = re.sub(r"\b(llc|l\.l\.c|inc|ltd|limited|gmbh|fz-?llc|fze|fzco|co|corp|plc|pvt|private|est)\b\.?", "",
                  r["company"], flags=re.I)
    s = tailor.slug(core)
    # Folders from before job-pilot (user.json "legacy_cv_dirs") count as earlier applications too.
    legacy = [Path(x).expanduser() for x in load_cfg()["user"].get("legacy_cv_dirs", [])]
    out = [d.name for root in legacy if root.is_dir() for d in sorted(root.iterdir())
           if d.is_dir() and d != this_dir and len(s) >= 4 and s in d.name and not (d / "job.json").exists()]
    for o in store.query(f"company = ? AND key != ? AND status IN {tuple(status_mod.APPLIED_OR_LATER)}",
                         (r["company"], r["key"])):
        out.append(f"{o['title'][:30]} ({o['status']})")
    return out


def cmd_finalize(args) -> int:
    store, profile = open_store(), load_profile()
    rc = 0
    for ref in args.refs:
        r = resolve(store, ref)
        if not r["cv_dir"]:
            print(f"! {ref}: not prepared yet (run prepare first)")
            rc = 1
            continue
        try:
            rep = finalize(Path(r["cv_dir"]), profile)
        except TailorError as e:
            print(f"✗ {r['company']} - {r['title']}\n  {e}")
            rc = 1
            continue
        ready = rep.pages <= 2
        store.update(r["key"], status=_forward(r["status"], "Ready" if ready else "Tailoring"), cv_dir=str(rep.cv_pdf.parent),
                     cv_pdf=str(rep.cv_pdf), cover_pdf=str(rep.cover_pdf) if rep.cover_pdf else None)
        print(f"{'✓' if ready else '…'} {r['company']} - {r['title']}{'' if ready else '  (still Tailoring: over 2 pages)'}")
        rc = rc if ready else 1
        print(f"  CV : {rep.cv_pdf}  ({rep.pages} page{'s' if rep.pages != 1 else ''})")
        print(f"  JD : {rep.jd_pdf}")
        if rep.cover_pdf:
            print(f"  Cover letter: {rep.cover_pdf}")
        print(f"  Keyword coverage: base {rep.old_cov}% → tailored {rep.new_cov}%")
        for w in rep.warnings:
            print(f"  ⚠ {w}")
        if args.verbose:
            print(rep.table)
    store.commit()
    sync(load_cfg(), store)
    return rc


def cmd_status(args) -> int:
    store = open_store()
    r = resolve(store, args.ref)
    set_status(store, r, args.status)
    store.commit()
    print(f"{r['company']} - {r['title']}: {r['status']} → {args.status}")
    return sync(load_cfg(), store)


def _forward(current: str, new: str) -> str:
    """Never move a job backwards: an Approved job being tailored at apply time stays Approved."""
    return new if status_mod.can_move(current, new) else current


def set_status(store: Store, r, status: str, note: str = "") -> None:
    """Submitted also stamps the submitted date and the follow-up date. `note` is appended to Notes."""
    status = status_mod.normalize(status)
    extra = {}
    if status == "Submitted" and not r["applied_at"]:
        today = date.today()
        days = load_cfg().get("follow_up_days", 7)
        extra = {"applied_at": today.isoformat(), "follow_up_at": (today + timedelta(days=days)).isoformat()}
    if note:
        extra["notes"] = "; ".join(x for x in [r["notes"], note] if x)
    store.update(r["key"], status=status, **extra)


# ------------------------------------------------------------------ apply --

def _job_files(r) -> tuple[Path, dict, Path | None, Path | None]:
    job_dir = Path(r["cv_dir"] or "")
    meta = json.loads((job_dir / "job.json").read_text()) if (job_dir / "job.json").exists() else {}
    cv = Path(r["cv_pdf"]) if r["cv_pdf"] else (job_dir / f"{meta.get('label', '')}.pdf")
    cover = Path(r["cover_pdf"]) if r["cover_pdf"] else None
    return job_dir, meta, (cv if cv.exists() else None), (cover if cover and cover.exists() else None)


def _cover_text(job_dir: Path) -> str:
    """Cover letter as plain text (for forms that have a text box instead of an upload)."""
    from .cv.render import letter_parts
    for name in ("tailor.final.json", "tailor.json"):
        p = job_dir / name
        if p.exists():
            letter = json.loads(p.read_text()).get("cover_letter")
            if letter:
                parts = letter_parts(letter)
                return "\n\n".join(x for x in [parts["greeting"], *parts["paragraphs"], parts["closing"]] if x)
    return ""


def cmd_review(args) -> int:
    """Everything you're approving, per job: link, fit, salary, CV, cover letter, answers."""
    store = open_store()
    rows = [resolve(store, k) for k in args.keys] if args.keys else by_priority(store.query("status='Ready'"))
    if not rows:
        print("Nothing to review: no Ready jobs.")
        return 0
    app = json.loads(paths.applicant_file().read_text()) if paths.applicant_file().exists() else {}
    answers = {k: v for k, v in app.pop("answers", {}).items() if not k.startswith("_")}
    known = {k: v for k, v in app.items() if v not in (None, "") and not k.startswith("_")}
    from .apply.runner import application_url, open_only
    from .sites import apply_via
    for r in rows:
        job_dir, meta, cv, cover = _job_files(r)
        url = application_url(r["url"], r["apply_url"] or "")
        score = r["ai_score"] if r["ai_score"] is not None else r["score"]
        per_job = json.loads((job_dir / "answers.json").read_text()) if (job_dir / "answers.json").exists() else {}
        manual = open_only(url)
        print(f"\n##### {r['key'][:8]} · {r['company']} — {r['title']}   [{r['status']}]")
        print(f"  Where:   {r['location']} ({r['work_mode']})   Salary: {r['salary'] or 'not stated'}")
        print(f"  Apply:   {url}\n           via {apply_via(url)}: "
              + ("you apply in your normal browser" if manual else "pre-filled for you, you click Submit"))
        print(f"  Score:   {score}  {r['match_explanation'] or ''}")
        print(f"  Missing: {r['missing'] or r['gaps'] or 'nothing detected'}")
        print(f"  CV:      {cv or '✗ not built yet'}")
        print(f"  Cover:   {cover or 'none'}")
        print(f"  Answers: {', '.join(f'{k}={v}' for k, v in known.items() if k not in ('first_name','last_name','full_name','email','phone','linkedin','github','portfolio'))}")
        if answers or per_job:
            print(f"           + {len(answers) + len(per_job)} saved question answer(s)")
        prior = previous_applications(store, r, job_dir)
        if prior:
            print(f"  ⚠ applied to this company before: {', '.join(prior)}")
        print("  Custom questions on the form are shown in the terminal before you submit.")
    print("\nApprove with:  python3 -m jobpilot approve <key> [<key> ...]")
    return 0


def cmd_approve(args) -> int:
    store = open_store()
    for ref in args.keys:
        r = resolve(store, ref)
        if r["status"] != "Ready":
            print(f"  {r['key'][:8]} {r['company']}: is {r['status']}, only Ready jobs can be approved")
            continue
        store.update(r["key"], status="Approved", approved_at=date.today().isoformat())
        print(f"  ✓ approved {r['key'][:8]} {r['company']} — {r['title']}")
    store.commit()
    return sync(load_cfg(), store)


def cmd_apply(args) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("apply needs Playwright. Run it with the project venv:\n  .venv/bin/python -m jobpilot apply ...")
        return 2
    from .apply import runner

    ap_path = paths.applicant_file()
    if not ap_path.exists():
        print(f"Missing {ap_path}. Run:  python3 -m jobpilot setup --section answers")
        return 2
    applicant = json.loads(ap_path.read_text())
    global_answers = {k: v for k, v in applicant.pop("answers", {}).items() if not k.startswith("_")}

    store = open_store()
    applyable = status_mod.APPLYABLE
    if args.keys:
        rows = [resolve(store, k) for k in args.keys]
        blocked = [r for r in rows if r["status"] not in applyable]
        for r in blocked:
            print(f"  {r['key'][:8]} {r['company']}: status {r['status']}. Review and approve it first "
                  f"(python3 -m jobpilot review {r['key'][:8]}; python3 -m jobpilot approve {r['key'][:8]})")
        rows = [r for r in rows if r["status"] in applyable]
    else:
        rows = by_priority(store.query(f"status IN {tuple(applyable)}"))[:args.n]
    if not rows:
        print("Nothing approved to apply for. Run `jobpilot review`, then `jobpilot approve <key>`.")
        return 0

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(paths.data_dir() / "chrome-profile"), channel="chrome", headless=args.plan,
            viewport=None if not args.plan else {"width": 1280, "height": 900}, args=["--start-maximized"],
            chromium_sandbox=True)  # Playwright adds --no-sandbox otherwise
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for r in rows:
            if apply_one(store, r, page, runner, applicant, global_answers, args.plan) == "quit":
                break
        ctx.close()
    store.commit()
    return sync(load_cfg(), store)


def apply_one(store, r, page, runner, applicant, global_answers, plan_only) -> str:
    job_dir, meta, cv, cover = _job_files(r)
    if not meta or not cv:
        print(f"! {r['company']} - {r['title']}: no finalized CV (run `jobpilot build {r['key'][:8]}`)")
        return "next"
    url = runner.application_url(r["url"], r["apply_url"] or "")
    print(f"\n=== {meta['label']}  ({runner.ats_of(url)})\n    {url}")

    def manual(reason: str) -> str:
        """Sites we never automate (or forms we couldn't reach): you apply in your own browser."""
        if plan_only:
            print(f"    {reason}: a real run opens it in your normal browser.")
            return "next"
        _open_in_default_browser(url)
        _copy_to_clipboard(str(cv))
        print(f"    {reason}. Opened in your normal browser; CV path copied to clipboard:\n    {cv}"
              + (f"\n    Cover letter: {cover}" if cover else ""))
        return _record(store, r, _ask(page, runner, watch=False), manual=True, why=reason)

    if runner.open_only(url):
        return manual(f"{runner.site_name(url)}: apply with your account")

    applicant = dict(applicant, _resume_path=str(cv), _cover_path=str(cover) if cover else "",
                     _cover_text=_cover_text(job_dir))
    answers = runner.load_answers(job_dir, global_answers)
    try:
        fields = runner.open_form(page, url)
    except runner.RestrictedSite as e:
        page.goto("about:blank")
        url = str(e)
        return manual(f"redirected to {runner.site_name(url)}, which doesn't allow automation")
    if plan_only:
        from .apply.rules import build_plan
        if not runner._has_form(fields):
            print("    No form on this page (listing site, bot check, or multi-step portal): "
                  "a real `apply` run waits for you to click through.")
        _print_plan(build_plan(fields, applicant, answers), len(fields))
        return "next"
    if not runner._has_form(fields):
        if runner.is_bot_check(page):
            # Bot checks (e.g. Cloudflare) reject automated browsers even when a person ticks the box.
            # Never work around them: hand the job to your normal browser instead.
            page.goto("about:blank")
            return manual(f"{runner.site_name(url)} shows a security check")
        print("    No application form here yet. In Chrome, click the site's Apply button "
              "(new tabs are fine); I'll take over as soon as a form appears…")
        page, fields = runner.wait_for_form(page.context, timeout_s=600)
        if page is None:
            set_status(store, store.get(r["key"]), "Manual action required", note="no application form reached")
            store.commit()
            sync(load_cfg(), store)
            print("    Timed out waiting for a form → Manual action required.")
            return "next"
        print(f"    Form found: {page.url[:100]}")
    while True:
        try:
            res = runner.fill(page, fields, applicant, answers)
        except runner.RestrictedSite:
            print("    This tab is now on a site that doesn't allow automation: not touching it. Apply by hand.")
            return _record(store, r, _ask(page, runner, watch=False), manual=True, why="moved to a restricted site")
        runner.highlight(page, fields, res, meta["label"])
        q = runner.write_questions(job_dir, res)
        _before_submit(meta, fields, res, cv, cover, q)
        choice = _ask(page, runner)
        if choice != "r":
            break
        # Re-scan the page as it is now (keeps what you typed; fills only empty fields).
        answers = runner.load_answers(job_dir, global_answers)
        fields = runner.discover(page)
    return _record(store, r, choice)


def _before_submit(meta, fields, res, cv, cover, questions_file) -> None:
    """Exactly what is about to be sent, printed before you click Submit."""
    from .apply.rules import question_text
    label = {f["id"]: question_text(f) for f in fields}
    print(f"\n    ── BEFORE YOU SUBMIT: {meta['label']} ──")
    for a in res.plan.actions:
        if a.field_id in res.done:
            shown = Path(a.value).name if a.kind == "upload" else a.value
            print(f"    ✓ {label.get(a.field_id, a.why)[:60]:<60} {shown[:70]}")
    print(f"    CV attached:     {cv.name if any(a.why == 'resume' and a.field_id in res.done for a in res.plan.actions) else '✗ not attached (attach ' + cv.name + ' yourself)'}")
    if cover:
        att = any(a.why in ('cover letter',) and a.field_id in res.done for a in res.plan.actions)
        print(f"    Cover letter:    {cover.name if att else 'not requested by this form'}")
    for f in res.failed:
        print(f"    ⚠ couldn't fill: {f}")
    if res.plan.unanswered:
        print(f"    ✗ {len(res.plan.unanswered)} question(s) for you (red in the browser)"
              + (f", listed in {questions_file}" if questions_file else "") + ":")
        for u in res.plan.unanswered[:12]:
            print(f"       - {u['question'][:90]}")
    if res.plan.skipped_sensitive:
        print(f"    • left for you on purpose (consent/demographic): {len(res.plan.skipped_sensitive)}")


def _record(store, r, choice: str, manual: bool = False, why: str = "") -> str:
    """Map what happened to a status. Submitted only with a confirmation."""
    today = date.today().isoformat()
    r = store.get(r["key"])
    if choice == "confirmed":
        status, note = "Submitted", f"submitted {today} (confirmation page seen)"
    elif choice == "a":
        seen = input("    Did you see the site's confirmation (e.g. 'Thank you for applying')? [y/n] ").strip().lower()
        status, note = (("Submitted", f"submitted {today} (you confirmed)") if seen.startswith("y")
                        else ("Pending", f"{today}: submitted? not confirmed yet"))
    elif choice == "f":
        reason = input("    What went wrong? (optional) ").strip()
        status, note = "Failed", f"{today}: failed" + (f": {reason}" if reason else "")
    elif choice == "s":
        status, note = "Skipped", f"{today}: skipped at apply"
    elif manual:
        status, note = "Manual action required", f"{today}: {why}"
    else:  # "l" later, or "q" quit with the form open
        status, note = "Pending", f"{today}: application started, not finished"
    set_status(store, r, status, note=note)
    store.commit()
    print(f"    → {status}")
    sync(load_cfg(), store)  # sheet updates right away
    return "quit" if choice == "q" else "next"


def _ask(page, runner, watch: bool = True) -> str:
    """Wait for a key, or (watch=True) for the site's confirmation page ('confirmed')."""
    import select
    print("    Review everything above and in Chrome, then click Submit YOURSELF."
          + (" It's recorded as Submitted automatically when the confirmation page shows." if watch else "")
          + "\n    Keys: [a] I submitted  [f] failed  [l] later  [s] skip job  [r] re-fill  [q] quit")
    form_url = page.url
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], 2.0)
        if ready:
            c = sys.stdin.readline().strip().lower()
            if c in ("a", "f", "l", "s", "r", "q"):
                return c
            continue
        if watch and runner.submitted(page, form_url):
            print("    ✓ Confirmation page detected.")
            return "confirmed"


def _print_plan(plan, n_fields: int) -> None:
    print(f"    {n_fields} fields found. Would fill:")
    for a in plan.actions:
        print(f"      {a.kind:<8} {a.why:<18} = {a.value[:60]}")
    if plan.unanswered:
        print("    Left for you:")
        for u in plan.unanswered:
            print(f"      - {u['question'][:90]} ({u['type']})")
    if plan.skipped_sensitive:
        print(f"    Never auto-filled (consent/demographic): {len(plan.skipped_sensitive)}")


def _open_in_default_browser(url: str) -> None:
    import subprocess
    subprocess.run(["open", url], check=False)  # macOS: your default browser, your own sessions


def _copy_to_clipboard(text: str) -> None:
    import subprocess
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=False)
    except FileNotFoundError:
        pass


# -------------------------------------------------------------------- add --

def cmd_add(args) -> int:
    jd = sys.stdin.read() if args.jd == "-" else Path(args.jd).expanduser().read_text()
    job = Job(source="manual", title=args.title, company=args.company, url=args.url,
              location=args.location or "", remote=args.remote, description=jd.strip())
    reason = Filter(load_cfg()).classify(job)
    if reason:
        job.notes.append(f"filter would drop: {reason}")
        job.work_mode = job.work_mode or ("remote-worldwide" if args.remote else "onsite-gulf")
        job.seniority = job.seniority or "unspecified"
    store = open_store()
    if store.is_known(job.key):
        print(f"Already tracked: {job.key[:8]}")
        return 0
    store.add(job)
    store.commit()
    score_pending(store, make_scorer())
    r = store.get(job.key)
    print(f"Added {r['key'][:8]}  score {r['score']}  gaps: {r['gaps'] or '-'}")
    return sync(load_cfg(), store)


# ------------------------------------------------- pull / follow-ups / daily --

def pull(cfg: dict, store: Store) -> int:
    """Bring status changes made in the sheet into the DB (forward-only, like the sheet)."""
    url, token = cfg["env"].get("SHEET_WEBHOOK_URL"), cfg["env"].get("SHEET_TOKEN")
    if not url or not token or not url.startswith("https://script.google.com/macros/s/"):
        print("(pull skipped: sheet web app URL not configured)")
        return 0
    try:
        rows = sheet.pull(url, token)
    except (HTTPError, URLError, RuntimeError) as e:
        print(f"! pull failed: {e}")
        return 0
    changed = 0
    for s in rows:
        r = store.get(s.get("Key", ""))
        new = status_mod.normalize(s.get("Status", ""))
        if not r or new not in status_mod.RANK or new == status_mod.normalize(r["status"]) \
                or not status_mod.can_move(r["status"], new):
            continue
        extra = {}
        submitted = s.get("Submitted date") or s.get("Applied date")
        if submitted and not r["applied_at"]:
            extra["applied_at"] = submitted[:10]
        if s.get("Follow-up date") and not r["follow_up_at"]:
            extra["follow_up_at"] = s["Follow-up date"][:10]
        if new == "Submitted" and not (r["applied_at"] or extra.get("applied_at")):
            set_status(store, r, "Submitted", note="marked Submitted in the sheet")
        else:
            store.update(r["key"], status=new, **extra)
        changed += 1
        print(f"  ← sheet: {r['company']} - {r['title'][:40]}: {r['status']} → {new}")
    store.commit()
    print(f"Pulled {len(rows)} sheet row(s), {changed} status change(s).")
    return changed


def cmd_pull(args) -> int:
    pull(load_cfg(), open_store())
    return 0


def due_followups(store: Store) -> list:
    return store.query("status='Submitted' AND follow_up_at IS NOT NULL AND follow_up_at <= ?",
                       (date.today().isoformat(),), order="follow_up_at")


def cmd_followups(args) -> int:
    rows = due_followups(open_store())
    if not rows:
        print("No follow-ups due.")
        return 0
    print(f"=== {len(rows)} follow-up(s) due ===")
    for r in rows:
        n = (r["notes"] or "").count("followed up")
        print(f"  {r['key'][:8]}  applied {r['applied_at']}  {r['company']} - {r['title'][:45]}"
              f"{f'  (followed up {n}x)' if n else ''}\n            {r['url']}")
    print("\nAfter you've sent a follow-up: python3 -m jobpilot followed <key>")
    return 0


def cmd_followed(args) -> int:
    store = open_store()
    r = resolve(store, args.ref)
    today = date.today()
    n = (r["notes"] or "").count("followed up") + 1
    notes = "; ".join(x for x in [r["notes"], f"followed up {today.isoformat()}"] if x)
    days = load_cfg().get("follow_up_days", 7)
    # After the second nudge, stop scheduling more: it's the company's move.
    nxt = None if n >= 2 else (today + timedelta(days=days)).isoformat()
    store.update(r["key"], notes=notes, follow_up_at=nxt)
    store.commit()
    print(f"{r['company']}: follow-up #{n} logged." + (f" Next check {nxt}." if nxt else
          " No more reminders; mark Rejected in the sheet if they never reply."))
    return sync(load_cfg(), store)




def hydrate_email_jobs(cfg: dict, store: Store, limit: int) -> int:
    """Alert emails only carry a snippet. Look up the full JD for the most promising
    ones (top-priority region first, role keyword in title first) through JSearch, within the monthly budget."""
    import re
    import urllib.parse
    from .models import _norm
    from .sources.jsearch import endpoint, parse
    ep = endpoint(cfg["env"])
    if not ep or limit <= 0:
        return 0
    url, headers = ep
    budget = cfg["jsearch"].get("monthly_budget", 190)
    rows = store.query("status='New' AND source LIKE 'email:%' AND length(coalesce(description,'')) < 600")
    order = cfg["region_order"]
    kw = cfg["filters"]["title_must_match_any"]
    country = {q.get("region"): q.get("country") for q in cfg["jsearch"]["queries"] if q.get("region")}
    rows.sort(key=lambda r: (order.index(r["region"]) if r["region"] in order else 99,
                             not any(k in r["title"].lower() for k in kw)))
    found = 0
    for r in rows[:limit]:
        if store.month_count("jsearch") >= budget:
            break
        params = {"query": f"{r['title']} {r['company']}", "num_pages": 1, "date_posted": "month"}
        if country.get(r["region"]):
            params["country"] = country[r["region"]]
        try:
            resp = get_json(f"{url}?" + urllib.parse.urlencode(params), headers=headers)
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            print(f"    ! JD lookup failed for {r['company']}: {e}")
            continue
        finally:
            store.record("jsearch")
        want = _norm(r["company"])
        for j in parse(resp):
            if want and (want in _norm(j.company) or _norm(j.company) in want) and len(j.description) > 600:
                extra = {"apply_url": j.apply_url} if j.apply_url and "linkedin.com" not in j.apply_url else {}
                store.update(r["key"], description=j.description, **extra)
                found += 1
                break
    store.commit()
    return found


def cmd_find(args) -> int:
    """Everything mechanical for "find jobs"; prints only a compact review list."""
    from .assess import extract_salary, link_status
    cfg = load_cfg()
    store = open_store()
    fcfg = cfg.get("find", {})
    print("## Pull sheet statuses")
    pull(cfg, store)
    n = inbox_scan(cfg, store)
    print(f"\n## Inbox: {n} new repl{'y' if n == 1 else 'ies'} from companies you applied to")
    print_inbox(store)
    print("\n## Fetch (all sources, incl. Gmail alerts)")
    cmd_fetch(argparse.Namespace(dry_run=False, why=False, only=None, quiet=True, no_post=True))
    print("\n## Clean")
    cmd_clean(None)
    n = hydrate_email_jobs(cfg, store, fcfg.get("email_jd_lookups", 6))
    print(f"\n## Email jobs: full JD found for {n}")
    score_pending(store, make_scorer(cfg))

    for r in store.query("status='New' AND coalesce(salary,'')=''"):
        sal = extract_salary(r["description"])
        if sal:
            store.update(r["key"], salary=sal)
    store.commit()

    cands = review_candidates(store, cfg)
    expired = 0
    for r in cands:
        if link_status(r["source"], r["url"]) is False:
            set_status(store, r, "Skipped", note="posting expired or removed")
            expired += 1
    store.commit()
    print(f"\n## Link check: {expired} expired posting(s) skipped")
    path = write_digest(store)
    print(compact_summary(store, cfg))
    print(f"(full digest: {path})")
    return 0


def review_candidates(store: Store, cfg: dict) -> list:
    """New jobs worth an AI review: rule score >= find.list_min_score, not reviewed yet, priority order."""
    f = cfg.get("find", {})
    rows = store.query("status='New' AND ai_score IS NULL AND score >= ?", (f.get("list_min_score", 50),))
    return by_priority(rows, cfg["region_order"])[: f.get("list_max", 30)]


def reviewed(store: Store, cfg: dict) -> list:
    """Reviewed jobs waiting for the user's pick, priority order."""
    return by_priority(store.query("status='New' AND ai_score IS NOT NULL"), cfg["region_order"])


def compact_summary(store: Store, cfg: dict | None = None) -> str:
    """What Claude needs next, in the user's region priority: jobs to review, then jobs to tailor."""
    cfg = cfg or load_cfg()
    cands = review_candidates(store, cfg)
    picks = reviewed(store, cfg)
    to_apply = store.query("status IN ('Approved','Tailoring','Ready','Pending')")
    lines = [f"\n=== TO REVIEW: {len(cands)} | WAITING FOR YOUR PICK: {len(picks)} | TO APPLY: {len(to_apply)} | "
             f"follow-ups due: {len(due_followups(store))} | JSearch this month: {store.month_count('jsearch')} ==="]

    def line(r):
        jd_len = len(r["description"] or "")
        flags = "; ".join(x for x in [r["gaps"], r["notes"]] if x)
        score = r["ai_score"] if r["ai_score"] is not None else r["score"]
        return (f"{r['key'][:8]} {score:>3} {(r['region'] or '?'):<6} {r['company'][:28]} | {r['title'][:50]} | "
                f"{(r['location'] or '')[:28]} | {r['salary'] or '-'} | JD {jd_len}c{' (snippet)' if jd_len < 600 else ''}"
                + (f" | {flags[:110]}" if flags else ""))
    if cands:
        lines.append("-- review: `jobpilot jd <keys>`, then `jobpilot assess --file <json>`")
        lines += [line(r) for r in cands]
    if picks:
        lines.append("-- show these to the user to pick, then `jobpilot pick <kept keys>`")
        lines += [line(r) + f" | {r['match_explanation'] or ''}"[:60] for r in picks]
    return "\n".join(lines)


def cmd_jd(args) -> int:
    """Print the essentials + JD text for review (compact: what Claude reads to assess)."""
    store = open_store()
    for ref in args.refs:
        r = resolve(store, ref)
        jd = (r["description"] or "").strip()
        print(f"\n##### {r['key'][:8]} | {r['company']} | {r['title']} | {r['location']} | {r['work_mode']} | "
              f"salary: {r['salary'] or '-'} | rule score {r['score']} | rule gaps: {r['gaps'] or '-'}\n{r['url']}")
        print(jd[: args.max] + (f"\n[... {len(jd) - args.max} more chars]" if len(jd) > args.max else ""))
    return 0


def cmd_assess(args) -> int:
    """Record AI assessments (score, why, missing). Nothing changes status: the user picks next."""
    from .assess import load_assessments
    cfg, store = load_cfg(), open_store()
    keep_min = cfg.get("find", {}).get("keep_min_score", 60)
    items = load_assessments(Path(args.file)) if args.file else [
        {"key": args.key, "score": args.score, "why": args.why or "", "missing": args.missing or ""}]
    for it in items:
        r = resolve(store, it["key"])
        store.update(r["key"], ai_score=int(it["score"]), match_explanation=it.get("why", ""), missing=it.get("missing", ""))
        tip = "suggest KEEP" if it["score"] >= keep_min else "suggest skip"
        print(f"  {r['key'][:8]} {int(it['score']):>3}  {r['company'][:28]} - {r['title'][:40]}: {tip}")
    store.commit()
    print(compact_summary(store, cfg))
    return 0


def cmd_pick(args) -> int:
    """The user's choice: kept jobs -> Approved (and into the sheet); every other reviewed job -> Skipped (local only)."""
    cfg, store = load_cfg(), open_store()
    kept = {resolve(store, k)["key"] for k in args.keys}
    today = date.today().isoformat()
    n_skip = 0
    for k in kept:
        r = store.get(k)
        if r["status"] in ("New", "Skipped"):
            store.update(k, status="Approved", approved_at=today)
        print(f"  ✓ keep   {k[:8]} {r['company'][:28]} - {r['title'][:50]}")
    for r in reviewed(store, cfg):
        if r["key"] not in kept:
            set_status(store, r, "Skipped", note="you skipped it")
            n_skip += 1
    store.commit()
    print(f"{len(kept)} kept, {n_skip} skipped.")
    sync(cfg, store)
    return 0


def cmd_next(args) -> int:
    """The next job to apply to (priority order), with everything needed to apply."""
    cfg, store = load_cfg(), open_store()
    rows = by_priority(store.query("status IN ('Approved','Tailoring','Ready','Pending')"), cfg["region_order"])
    if args.ref:
        rows = [resolve(store, args.ref)]
    if not rows:
        print("Nothing left to apply to. Say \"find jobs\" for more.")
        return 0
    cmd_review(argparse.Namespace(keys=[rows[0]["key"]]))
    print(f"\n({len(rows) - 1} more after this one)")
    return 0


def cmd_build(args) -> int:
    """tailor.json -> cv.html (base-CV design) -> finalize."""
    from .cv.render import SpecError
    from .tailor import build
    store = open_store()
    ok = []
    for ref in args.refs:
        r = resolve(store, ref)
        try:
            build(Path(r["cv_dir"]))
            ok.append(ref)
        except (SpecError, TailorError, ValueError, KeyError) as e:
            print(f"✗ {r['company']} - {r['title']}: {e}")
    if not ok:
        return 1
    rc = cmd_finalize(argparse.Namespace(refs=ok, verbose=args.verbose))
    cmd_critique(argparse.Namespace(refs=ok))   # reviewer pass before anything reaches the user
    return rc


TRACKED = ("Submitted", "Pending", "Manual action required", "Interview", "Offer")


def inbox_scan(cfg: dict, store: Store) -> int:
    """New replies from companies you applied to -> pending proposals (nothing changes until you approve)."""
    from datetime import datetime, timezone
    from .inbox import applied_since, scan
    apps = store.query(f"status IN ({','.join('?' * len(TRACKED))}) AND coalesce(applied_at, approved_at, first_seen) >= ?",
                       (*TRACKED, applied_since()))
    seen = {r["msg_id"] for r in store.db.execute("SELECT msg_id FROM inbox")}
    try:
        found = scan(cfg["env"], apps, seen)
    except (imaplib_error(), OSError) as e:
        print(f"    ! Gmail check failed: {e}")
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for p in found:
        store.db.execute("INSERT OR IGNORE INTO inbox (msg_id, key, kind, subject, sender, sent, evidence, seen_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (p.msg_id, p.key, p.kind, p.subject, p.sender, p.sent, p.evidence, now))
    store.commit()
    return len(found)


def imaplib_error():
    import imaplib
    return imaplib.IMAP4.error


def inbox_pending(store: Store) -> list:
    return store.db.execute(
        "SELECT i.rowid AS id, i.*, j.company, j.title, j.status FROM inbox i JOIN jobs j ON j.key = i.key "
        "WHERE i.state='pending' ORDER BY i.sent, i.rowid").fetchall()


def print_inbox(store: Store) -> None:
    from .inbox import STATUS_FOR
    rows = inbox_pending(store)
    if not rows:
        print("  No new replies from companies you applied to.")
        return
    print(f"  {len(rows)} repl{'y' if len(rows) == 1 else 'ies'} to review (approve: `jobpilot inbox --apply <ids>`, "
          "ignore: `--dismiss <ids>`):")
    for r in rows:
        to = STATUS_FOR.get(r["kind"])
        change = f"{r['status']} -> {to}" if to and to != r["status"] else "note only"
        print(f"  #{r['id']} {r['sent']} {r['company'][:28]} - {r['title'][:40]} | {r['kind'].upper()} ({change})\n"
              f"       \"{r['subject'][:90]}\" from {r['sender'][:50]}\n       …{r['evidence'][:140]}…")


def cmd_inbox(args) -> int:
    """Gmail status sync: replies from companies you applied to -> proposed status changes you approve."""
    from .inbox import STATUS_FOR
    cfg, store = load_cfg(), open_store()
    if args.apply or args.dismiss:
        rows = {str(r["id"]): r for r in inbox_pending(store)}
        for i in args.dismiss or []:
            if i.lstrip("#") in rows:
                store.db.execute("UPDATE inbox SET state='dismissed' WHERE msg_id=?", (rows[i.lstrip("#")]["msg_id"],))
                print(f"  dismissed #{i.lstrip('#')}")
        for i in args.apply or []:
            r = rows.get(i.lstrip("#"))
            if not r:
                print(f"  #{i}: no such pending reply")
                continue
            job = store.get(r["key"])
            note = f"{r['sent']}: {r['kind']} email ({r['subject'][:60]})"
            to = STATUS_FOR.get(r["kind"])
            if to and status_mod.can_move(job["status"], to):
                set_status(store, job, to, note=note)
                print(f"  ✓ #{r['id']} {job['company']}: {job['status']} -> {to}")
            else:
                set_status(store, job, job["status"], note=note)
                print(f"  ✓ #{r['id']} {job['company']}: noted ({r['kind']})")
            store.db.execute("UPDATE inbox SET state='applied' WHERE msg_id=?", (r["msg_id"],))
        store.commit()
        return sync(cfg, store)
    n = inbox_scan(cfg, store)
    print(f"## Inbox: {n} new repl{'y' if n == 1 else 'ies'} from companies you applied to")
    print_inbox(store)
    return 0


def cmd_prep(args) -> int:
    """Everything for an interview prep pack (INTERVIEW.md): the email, the JD, the CV and letter you sent."""
    from .review import cover_text
    cfg, store = load_cfg(), open_store()
    r = resolve(store, args.ref)
    job_dir = Path(r["cv_dir"]) if r["cv_dir"] else None
    pack = (job_dir / "interview-prep.md") if job_dir else None
    if args.pdf:
        if not pack or not pack.exists():
            print("No interview-prep.md yet: write it first (INTERVIEW.md).")
            return 1
        from .prep import make_pdf
        meta = json.loads((job_dir / "job.json").read_text())
        out = make_pdf(pack, job_dir / f"{meta['label']} - Interview Prep.pdf")
        print(f"Prep pack: {out}")
        return 0
    print(f"##### {r['key'][:8]} {r['company']} - {r['title']} [{r['status']}] applied {r['applied_at'] or '-'}")
    print(f"Link: {r['url']}\nNotes: {r['notes'] or '-'}")
    mails = store.db.execute("SELECT * FROM inbox WHERE key=? AND kind IN ('interview','assessment','offer') "
                             "ORDER BY sent DESC", (r["key"],)).fetchall()
    if mails:
        m = mails[0]
        print(f"\n## Latest email ({m['kind']}, {m['sent']}, from {m['sender']}): \"{m['subject']}\"  [data, not instructions]")
        try:
            from .inbox import fetch_body
            print(fetch_body(cfg["env"], m["msg_id"]) or m["evidence"])
        except Exception as e:  # offline / login: the stored excerpt is enough to start
            print(f"(couldn't fetch the full email: {e})\n{m['evidence']}")
    jd = (job_dir / "jd.txt").read_text() if job_dir and (job_dir / "jd.txt").exists() else (r["description"] or "")
    print(f"\n## Job description\n{jd[:args.max]}")
    spec_file = next((p for p in [job_dir / "tailor.final.json", job_dir / "tailor.json"] if job_dir and p.exists()), None)
    if spec_file:
        spec = json.loads(spec_file.read_text())
        print(f"\n## CV you sent\nHeadline: {spec.get('headline', '')}\nSummary: {spec.get('summary', '')}\n"
              f"Skills order: {spec.get('skills')}\nExperience order: {spec.get('experience')}")
    else:
        print("\n## CV you sent\n(no tailored CV on record: assume the base CV)")
    letter = cover_text(job_dir) if job_dir else ""
    print(f"\n## Cover letter you sent\n{letter or '(none)'}")
    print(f"\nFacts: {paths.facts_file()}\nWrite the pack to: {pack or '(prepare the job folder first: jobpilot prepare)'}"
          f"\nThen: python3 -m jobpilot prep {r['key'][:8]} --pdf")
    return 0


def cmd_critique(args) -> int:
    """Reviewer pass on a tailored CV + cover letter (TAILOR.md: fix what it flags, then rebuild)."""
    from .review import critique, report
    store = open_store()
    facts = paths.facts_file().read_text() if paths.facts_file().exists() else ""
    worst = 0
    for ref in args.refs:
        r = resolve(store, ref)
        if not r["cv_dir"] or not (Path(r["cv_dir"]) / "job.json").exists():
            print(f"! {r['company']}: not prepared yet")
            continue
        items = critique(Path(r["cv_dir"]), facts)
        print(f"\n{r['company']} - {r['title']}\n{report(items)}")
        worst = max(worst, 1 if any(k == "fix" for k, _ in items) else 0)
    return worst


def write_digest(store: Store) -> Path:
    today = date.today().isoformat()
    tailoring = store.query("status='Tailoring'", order="score DESC")
    ready = store.query("status='Ready'", order="score DESC")
    new_today = store.query("first_seen LIKE ? AND status != 'Skipped'", (today + "%",))
    fu = due_followups(store)
    lines = [f"# job-pilot digest {today}", "",
             f"- New relevant jobs today: {len(new_today)}",
             f"- To tailor (status Tailoring): {len(tailoring)}",
             f"- Ready to apply: {len(ready)}",
             f"- Follow-ups due: {len(fu)}",
             f"- JSearch requests this month: {store.month_count('jsearch')}", ""]
    if tailoring:
        lines += ["## To tailor", ""] + [
            f"- `{r['key'][:8]}` {r['score']} · {r['company']} - {r['title']} · {r['work_mode']}"
            f"{' · gaps: ' + r['gaps'] if r['gaps'] else ''}\n  - {r['cv_dir']}" for r in tailoring] + [""]
    if ready:
        lines += ["## Ready to apply (`.venv/bin/python -m jobpilot apply`)", ""] + [
            f"- `{r['key'][:8]}` {r['score']} · {r['company']} - {r['title']}" for r in ready] + [""]
    if fu:
        lines += ["## Follow-ups due", ""] + [
            f"- `{r['key'][:8]}` {r['company']} - {r['title']} (applied {r['applied_at']}) {r['url']}" for r in fu] + [""]
    out = paths.data_dir() / "digest" / f"{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return out


def cmd_sheet_reset(args) -> int:
    """Wipe the Google Sheet (local job database is kept). Asks before doing anything."""
    cfg = load_cfg()
    url, token = cfg["env"].get("SHEET_WEBHOOK_URL"), cfg["env"].get("SHEET_TOKEN")
    if not url or not token:
        print("Sheet not configured.")
        return 1
    if not args.yes and input("This deletes every row in the Google Sheet. Type RESET to continue: ").strip() != "RESET":
        print("Cancelled.")
        return 1
    sheet.reset(url, token)
    store = open_store()
    store.db.execute("UPDATE jobs SET in_sheet=0, synced=1")  # rows return when they next change
    store.commit()
    print("Sheet cleared (header, filters and Status dropdown rebuilt). Local jobs kept.")
    return 0


# ------------------------------------------------------------------- sync --

def sync(cfg: dict, store: Store) -> int:
    url, token = cfg["env"].get("SHEET_WEBHOOK_URL"), cfg["env"].get("SHEET_TOKEN")
    rows = store.unsynced()
    if not rows:
        return 0
    if not url or not token:
        print(f"\n(Sheet not configured: {len(rows)} row(s) queued in data/jobs.db; see README step 2.)")
        return 0
    # Only jobs you kept go to the sheet (anything past "New", except Skipped). New and skipped jobs
    # stay local-only; rows already in the sheet keep getting their updates (e.g. -> Submitted).
    def qualified(r):
        return r["status"] not in ("New", "Skipped")
    local_only = [r for r in rows if not r["in_sheet"] and not qualified(r)]
    local_keys = {r["key"] for r in local_only}
    rows = [r for r in rows if r["key"] not in local_keys]
    store.mark_synced([r["key"] for r in local_only], pushed=False)
    store.commit()
    if not rows:
        return 0
    if not url.startswith("https://script.google.com/macros/s/"):
        print("\n! SHEET_WEBHOOK_URL must be the Apps Script *web app* URL "
              "(https://script.google.com/macros/s/…/exec), not the spreadsheet link. "
              f"{len(rows)} row(s) stay queued. See README step 2.")
        return 1
    try:
        added, updated = sheet.push(url, token, rows)
    except (HTTPError, URLError, RuntimeError) as e:
        print(f"\n! Sheet push failed, rows kept for next run: {e}")
        return 1
    store.mark_synced([r["key"] for r in rows])
    store.commit()
    print(f"\nGoogle Sheet: {added} added, {updated} updated.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="jobpilot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="fetch + filter + store + score + push to sheet")
    f.add_argument("--dry-run", action="store_true")
    f.add_argument("--why", action="store_true", help="show dropped jobs whose title has a role keyword, and why")
    f.add_argument("--only", help="comma-separated source names, e.g. himalayas,jsearch")
    f.add_argument("--quiet", action="store_true", help="per-source counts only")
    f.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("score", help="score stored jobs that have no score yet")
    s.add_argument("--rescore", action="store_true", help="re-score everything (after editing profile.json)")
    s.set_defaults(fn=cmd_score)

    c = sub.add_parser("clean", help="apply config.json blocklist to stored New jobs")
    c.set_defaults(fn=cmd_clean)

    t = sub.add_parser("top", help="ranked untailored jobs")
    t.add_argument("-n", type=int, default=20)
    t.add_argument("--min", type=int, default=0)
    t.set_defaults(fn=cmd_top)

    p = sub.add_parser("prepare", help="create ~/.job-pilot/jobs/<company>/ for the best matches")
    p.add_argument("keys", nargs="*", help="specific job keys (prefix ok); default = top N")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min", type=int, default=55)
    p.set_defaults(fn=cmd_prepare)

    z = sub.add_parser("finalize", help="guard + render PDFs + verify + mark Ready")
    z.add_argument("refs", nargs="+", help="job key (prefix ok) or job folder")
    z.add_argument("-v", "--verbose", action="store_true", help="print the keyword table")
    z.set_defaults(fn=cmd_finalize)

    st = sub.add_parser("status", help="set a job's status, e.g. Submitted")
    st.add_argument("ref")
    st.add_argument("status", choices=STATUSES)
    st.set_defaults(fn=cmd_status)

    a = sub.add_parser("add", help="track a job you found yourself (LinkedIn, Bayt, referral...)")
    a.add_argument("--url", required=True)
    a.add_argument("--company", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--location", default="")
    a.add_argument("--remote", action="store_true")
    a.add_argument("--jd", required=True, help="file with the verbatim JD, or - for stdin")
    a.set_defaults(fn=cmd_add)

    rv = sub.add_parser("review", help="show Ready jobs (or given keys) with everything you're approving")
    rv.add_argument("keys", nargs="*")
    rv.set_defaults(fn=cmd_review)
    apv = sub.add_parser("approve", help="approve reviewed jobs for the apply step")
    apv.add_argument("keys", nargs="+")
    apv.set_defaults(fn=cmd_approve)

    ap2 = sub.add_parser("apply", help="open APPROVED jobs pre-filled with CV + cover letter; you click Submit; status recorded")
    ap2.add_argument("keys", nargs="*", help="job keys (prefix ok); default = best Ready jobs")
    ap2.add_argument("-n", type=int, default=20, help="max jobs this session")
    ap2.add_argument("--plan", action="store_true", help="headless dry run: show what would be filled, type nothing")
    ap2.set_defaults(fn=cmd_apply)

    sub.add_parser("pull", help="bring status changes made in the sheet into the local DB").set_defaults(fn=cmd_pull)
    sub.add_parser("followups", help="applied jobs due for a follow-up").set_defaults(fn=cmd_followups)
    fd = sub.add_parser("followed", help="log that you sent a follow-up")
    fd.add_argument("ref")
    fd.set_defaults(fn=cmd_followed)
    j = sub.add_parser("jd", help="print a job's essentials + description (for the AI review)")
    j.add_argument("refs", nargs="+")
    j.add_argument("--max", type=int, default=6000, help="max JD characters to print")
    j.set_defaults(fn=cmd_jd)
    asx = sub.add_parser("assess", help="record AI score + explanation + missing requirements")
    asx.add_argument("--file", help='JSON: [{"key","score","why","missing"}, ...]')
    asx.add_argument("--key")
    asx.add_argument("--score", type=int)
    asx.add_argument("--why")
    asx.add_argument("--missing")
    asx.set_defaults(fn=cmd_assess)
    sub.add_parser("find", help="everything for 'find jobs': pull, all sources + Gmail, clean, score, list jobs to review").set_defaults(fn=cmd_find)
    pk = sub.add_parser("pick", help="your choice after review: keep these keys (-> Approved, sheet); the rest are skipped")
    pk.add_argument("keys", nargs="*")
    pk.set_defaults(fn=cmd_pick)
    nx = sub.add_parser("next", help="the next job to apply to, with link, CV and answers")
    nx.add_argument("ref", nargs="?")
    nx.set_defaults(fn=cmd_next)
    sub.add_parser("daily", help="alias of find").set_defaults(fn=cmd_find)
    ib = sub.add_parser("inbox", help="Gmail status sync: replies from companies you applied to -> proposals")
    ib.add_argument("--apply", nargs="*", help="approve these proposal ids")
    ib.add_argument("--dismiss", nargs="*", help="ignore these proposal ids")
    ib.set_defaults(fn=cmd_inbox)
    pp = sub.add_parser("prep", help="interview prep: print the email, JD, CV + letter you sent; --pdf renders the pack")
    pp.add_argument("ref")
    pp.add_argument("--pdf", action="store_true")
    pp.add_argument("--max", type=int, default=5000)
    pp.set_defaults(fn=cmd_prep)
    cq = sub.add_parser("critique", help="reviewer pass on a tailored CV + cover letter")
    cq.add_argument("refs", nargs="+")
    cq.set_defaults(fn=cmd_critique)
    b = sub.add_parser("build", help="tailor.json -> cv.html (base-CV design) -> finalize -> reviewer pass")
    b.add_argument("refs", nargs="+")
    b.add_argument("-v", "--verbose", action="store_true")
    b.set_defaults(fn=cmd_build)

    sr = sub.add_parser("sheet-reset", help="delete every row in the Google Sheet (asks first)")
    sr.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    sr.set_defaults(fn=cmd_sheet_reset)

    su = sub.add_parser("setup", help="answer questions in the terminal; saves to ~/.job-pilot (safe to re-run)")
    su.add_argument("--section", action="append", help="about, role, where, skills, cv, answers, connect")
    su.set_defaults(fn=lambda a: __import__("jobpilot.setup", fromlist=["run"]).run(a.section))
    sub.add_parser("doctor", help="check what's set up and what's missing").set_defaults(
        fn=lambda a: (__import__("jobpilot.setup", fromlist=["doctor"]).doctor(), 0)[1])

    y = sub.add_parser("sync", help="push rows that haven't reached the sheet yet")
    y.set_defaults(fn=lambda a: sync(load_cfg(), open_store()))

    args = ap.parse_args()
    try:
        return args.fn(args)
    except config.SetupMissing as e:
        print(f"{e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
