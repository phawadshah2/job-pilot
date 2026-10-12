"""job-pilot CLI.

Daily
    python3 -m jobpilot fetch              # fetch, filter, store, score, push to the sheet
    python3 -m jobpilot top                # ranked list of untailored jobs
    python3 -m jobpilot prepare --top 10   # set up ~/.job-pilot/jobs/<company>/ for the best matches
    (Claude tailors each cv.html per TAILOR.md)
    python3 -m jobpilot finalize <key>     # guard, render both PDFs, verify, mark Ready
    .venv/bin/python -m jobpilot apply     # pre-fill each Ready job in Chrome; YOU click Submit
    python3 -m jobpilot status <key> Applied

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
from .filters import Filter
from .http import HTTPError, URLError
from .http import get_json
from .hydrate import fetch_description
from .models import Job
from .scoring import Scorer
from .sources import SOURCES
from .store import Store
from .tailor import TailorError, finalize, prepare

STATUSES = ["New", "Tailoring", "Ready", "Applied", "Interview", "Offer", "Rejected", "Skipped"]
STATUS_RANK = {"New": 0, "Tailoring": 1, "Ready": 2, "Applied": 3, "Interview": 4, "Offer": 5, "Rejected": 5, "Skipped": 5}


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
    """Re-apply the blocklist to stored jobs that haven't been acted on yet."""
    store, flt = open_store(), Filter(load_cfg())
    n = 0
    for r in store.query("status IN ('New', 'Tailoring')"):
        if flt.blocked(r["company"], r["source"], r["url"]):
            notes = "; ".join(x for x in [r["notes"], "blocklisted"] if x)
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
        store.update(r["key"], status="Tailoring", cv_dir=str(d), notes=notes)
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
    for o in store.query("company = ? AND key != ? AND status IN ('Applied','Interview','Offer','Rejected')",
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
        store.update(r["key"], status="Ready" if ready else "Tailoring", cv_dir=str(rep.cv_pdf.parent))
        print(f"{'✓' if ready else '…'} {r['company']} - {r['title']}{'' if ready else '  (still Tailoring: over 2 pages)'}")
        rc = rc if ready else 1
        print(f"  CV : {rep.cv_pdf}  ({rep.pages} page{'s' if rep.pages != 1 else ''})")
        print(f"  JD : {rep.jd_pdf}")
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


def set_status(store: Store, r, status: str) -> None:
    extra = {}
    if status == "Applied" and not r["applied_at"]:
        today = date.today()
        days = load_cfg().get("follow_up_days", 7)
        extra = {"applied_at": today.isoformat(), "follow_up_at": (today + timedelta(days=days)).isoformat()}
    store.update(r["key"], status=status, **extra)


# ------------------------------------------------------------------ apply --

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
    if args.keys:
        rows = [resolve(store, k) for k in args.keys]
    else:
        rows = by_priority(store.query("status='Ready'", order="score DESC, first_seen"))[:args.n]
    if not rows:
        print("No Ready jobs. Tailor + finalize some first.")
        return 0

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(paths.data_dir() / "chrome-profile"), channel="chrome", headless=args.plan,
            viewport=None if not args.plan else {"width": 1280, "height": 900}, args=["--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for r in rows:
            if apply_one(store, r, page, runner, applicant, global_answers, args.plan) == "quit":
                break
        ctx.close()
    store.commit()
    return sync(load_cfg(), store)


def apply_one(store, r, page, runner, applicant, global_answers, plan_only) -> str:
    job_dir = Path(r["cv_dir"] or "")
    meta_p = job_dir / "job.json"
    if not meta_p.exists():
        print(f"! {r['company']} - {r['title']}: no tailored CV folder (prepare + finalize first)")
        return "next"
    meta = json.loads(meta_p.read_text())
    cv = job_dir / f"{meta['label']}.pdf"
    if not cv.exists():
        print(f"! {meta['label']}: CV PDF missing; run finalize")
        return "next"
    url = runner.application_url(r["url"], r["apply_url"] or "")
    print(f"\n=== {meta['label']}  (score {r['score']}, {runner.ats_of(url)})\n    {url}")

    if runner.OPEN_ONLY.search(url) and plan_only:
        print(f"    {runner.site_name(url)}: never automated; a real run opens it in your normal browser.")
        return "next"
    if runner.OPEN_ONLY.search(url):
        # Opened in your everyday browser, NOT the automated Chrome: LinkedIn & co. flag
        # automation-controlled browsers, and we never want your account signed in there.
        _open_in_default_browser(url)
        _copy_to_clipboard(str(cv))
        print(f"    {runner.site_name(url)} doesn't allow automated applications: opened it in your normal browser.\n"
              f"    Apply by hand there. CV path copied to clipboard:\n    {cv}")
    else:
        applicant = dict(applicant, _resume_path=str(cv))
        answers = runner.load_answers(job_dir, global_answers)
        try:
            fields = runner.open_form(page, url)
        except runner.RestrictedSite as e:
            page.goto("about:blank")
            _open_in_default_browser(str(e))
            _copy_to_clipboard(str(cv))
            print(f"    Redirected to {runner.site_name(str(e))}, which doesn't allow automation: opened it in your "
                  f"normal browser instead. Apply by hand. CV path copied to clipboard.")
            choice = _ask(page, runner, watch=False)
            if choice in ("a", "s"):
                set_status(store, store.get(r["key"]), "Applied" if choice == "a" else "Skipped")
                store.commit()
                sync(load_cfg(), store)
            return "quit" if choice == "q" else "next"
        if plan_only:
            from .apply.rules import build_plan
            if not runner._has_form(fields):
                print("    No form on this page (listing site, bot check, or multi-step portal): "
                      "a real `apply` run waits for you to click through.")
            _print_plan(build_plan(fields, applicant, answers), len(fields))
            return "next"
        if not runner._has_form(fields):
            if runner.is_bot_check(page):
                print("    The site is showing a security check: complete it yourself in the Chrome window.")
            print("    No application form here yet. In Chrome, click the site's Apply button "
                  "(new tabs are fine); I'll take over as soon as a form appears…")
            page, fields = runner.wait_for_form(page.context, timeout_s=600)
            if page is None:
                print("    Timed out waiting for a form; leaving this job Ready.")
                return "next"
            print(f"    Form found: {page.url[:100]}")
        while True:
            try:
                res = runner.fill(page, fields, applicant, answers)
            except runner.RestrictedSite:
                print("    This tab is now on a site that doesn't allow automation: not touching it. Apply by hand.")
                choice = _ask(page, runner, watch=False)
                break
            runner.highlight(page, fields, res, meta["label"])
            q = runner.write_questions(job_dir, res)
            print(f"    filled {len(res.done)} field(s) ({', '.join(sorted({a.why for a in res.plan.actions})) or 'nothing new'})")
            for f in res.failed:
                print(f"    ⚠ {f}")
            if res.plan.unanswered:
                print(f"    {len(res.plan.unanswered)} question(s) for you (red in the browser)" + (f", listed in {q}" if q else ""))
            if res.plan.skipped_sensitive:
                print(f"    left for you on purpose (consent/demographic): {len(res.plan.skipped_sensitive)}")
            choice = _ask(page, runner)
            if choice != "r":
                break
            # Re-scan the page as it is now (keeps what you typed; fills only empty fields).
            answers = runner.load_answers(job_dir, global_answers)
            fields = runner.discover(page)
        if choice == "q":
            return "quit"
        if choice in ("a", "s"):
            set_status(store, store.get(r["key"]), "Applied" if choice == "a" else "Skipped")
            store.commit()
            print("    → " + ("Applied (follow-up date set)" if choice == "a" else "Skipped"))
            sync(load_cfg(), store)  # sheet updates right away, not at the end
        return "next"
    choice = _ask(page, runner, watch=False)
    if choice in ("a", "s"):
        set_status(store, store.get(r["key"]), "Applied" if choice == "a" else "Skipped")
        store.commit()
        sync(load_cfg(), store)
    return "quit" if choice == "q" else "next"


def _ask(page, runner, watch: bool = True) -> str:
    """Wait for a key, or (watch=True) for the site's confirmation page, which counts as 'a'."""
    import select
    print("    Review in Chrome and click Submit YOURSELF. "
          + ("It's marked Applied automatically when the confirmation page shows. " if watch else "")
          + "Or press: [a] submitted  [s] skip  [r] re-fill  [l] later  [q] quit")
    form_url = page.url
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], 2.0)
        if ready:
            c = sys.stdin.readline().strip().lower()
            if c in ("a", "s", "r", "l", "q"):
                return c
            continue
        if watch and runner.submitted(page, form_url):
            print("    ✓ Confirmation page detected: marking Applied.")
            return "a"


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
        new = s.get("Status", "")
        if not r or new not in STATUS_RANK or STATUS_RANK[new] <= STATUS_RANK.get(r["status"], 0):
            continue
        extra = {}
        if s.get("Applied date") and not r["applied_at"]:
            extra["applied_at"] = s["Applied date"][:10]
        if s.get("Follow-up date") and not r["follow_up_at"]:
            extra["follow_up_at"] = s["Follow-up date"][:10]
        if new == "Applied" and not (r["applied_at"] or extra.get("applied_at")):
            set_status(store, r, "Applied")
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
    return store.query("status='Applied' AND follow_up_at IS NOT NULL AND follow_up_at <= ?",
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
    """Everything mechanical for "find jobs", printing only a compact summary."""
    cfg = load_cfg()
    store = open_store()
    fcfg = cfg.get("find", {})
    print("## Pull sheet statuses")
    pull(cfg, store)
    print("\n## Fetch (all sources, incl. Gmail alerts)")
    cmd_fetch(argparse.Namespace(dry_run=False, why=False, only=None, quiet=True, no_post=True))
    print("\n## Clean")
    cmd_clean(None)
    n = hydrate_email_jobs(cfg, store, fcfg.get("email_jd_lookups", 6))
    print(f"\n## Email jobs: full JD found for {n}")
    score_pending(store, make_scorer(cfg))
    print("\n## Prepare (score >= %d, top %d)" % (fcfg.get("prepare_min_score", 70), fcfg.get("prepare_top", 12)))
    cmd_prepare(argparse.Namespace(keys=[], top=fcfg.get("prepare_top", 12), min=fcfg.get("prepare_min_score", 70)))
    path = write_digest(store)
    print(compact_summary(store))
    print(f"(full digest: {path})")
    return 0


def compact_summary(store: Store) -> str:
    """One line per job to tailor, in the user's region priority: all Claude needs before reading each jd.txt."""
    rows = store.query("status='Tailoring'", order="score DESC")
    rows = by_priority(rows)
    ready = store.query("status='Ready'")
    lines = [f"\n=== TO TAILOR: {len(rows)} | Ready: {len(ready)} | follow-ups due: {len(due_followups(store))} "
             f"| JSearch this month: {store.month_count('jsearch')} ==="]
    for r in rows:
        jd_len = len(r["description"] or "")
        flags = "; ".join(x for x in [r["gaps"], r["notes"]] if x)
        lines.append(f"{r['key'][:8]} {r['score']:>3} {(r['region'] or '?'):<6} {r['company'][:28]} | {r['title'][:50]} | "
                     f"{(r['location'] or '')[:28]} | JD {jd_len}c{' (snippet)' if jd_len < 600 else ''}"
                     + (f" | {flags[:120]}" if flags else ""))
    return "\n".join(lines)


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
    return cmd_finalize(argparse.Namespace(refs=ok, verbose=args.verbose))


def cmd_daily(args) -> int:
    """The unattended morning run (minus tailoring, which Claude does per TAILOR.md)."""
    cfg = load_cfg()
    store = open_store()
    d = cfg.get("daily", {})
    print("## 1. Pull status changes from the sheet")
    pull(cfg, store)
    print("\n## 2. Fetch + score")
    cmd_fetch(argparse.Namespace(dry_run=False, why=False, only=None))
    print("\n## 3. Clean (blocklist)")
    cmd_clean(None)
    print("\n## 4. Prepare the best matches for tailoring")
    cmd_prepare(argparse.Namespace(keys=[], top=d.get("prepare_top", 10), min=d.get("prepare_min_score", 60)))
    path = write_digest(store)
    print(f"\nDigest: {path}")
    return 0


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
    # Jobs skipped before they ever reached the sheet (blocklisted spam, etc.) stay local-only.
    local_only = [r for r in rows if r["status"] == "Skipped" and not r["in_sheet"]]
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

    st = sub.add_parser("status", help="set a job's status, e.g. Applied")
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

    ap2 = sub.add_parser("apply", help="open Ready jobs in Chrome, pre-fill + attach CV (you click Submit)")
    ap2.add_argument("keys", nargs="*", help="job keys (prefix ok); default = best Ready jobs")
    ap2.add_argument("-n", type=int, default=20, help="max jobs this session")
    ap2.add_argument("--plan", action="store_true", help="headless dry run: show what would be filled, type nothing")
    ap2.set_defaults(fn=cmd_apply)

    sub.add_parser("pull", help="bring status changes made in the sheet into the local DB").set_defaults(fn=cmd_pull)
    sub.add_parser("followups", help="applied jobs due for a follow-up").set_defaults(fn=cmd_followups)
    fd = sub.add_parser("followed", help="log that you sent a follow-up")
    fd.add_argument("ref")
    fd.set_defaults(fn=cmd_followed)
    sub.add_parser("find", help="everything for 'find jobs': pull, all sources + Gmail, clean, score, prepare, compact summary").set_defaults(fn=cmd_find)
    sub.add_parser("daily", help="alias of find").set_defaults(fn=cmd_find)
    b = sub.add_parser("build", help="tailor.json -> cv.html (base-CV design) -> finalize")
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
