"""Effective settings = generic defaults (repo config.json) + the user's answers
(~/.job-pilot/user.json, written by `jobpilot setup`) + secrets (~/.job-pilot/.env).

Everything role- or person-specific is derived here, so the rest of the code never
hardcodes a role (Flutter...), a country or a person.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import paths

POINTS_BY_RANK = [15, 13, 12, 11, 10, 10, 9, 9, 8, 8, 8, 8, 8]


class SetupMissing(Exception):
    """Raised when ~/.job-pilot has no user.json yet: run `jobpilot setup`."""


def load_env(path: Path | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    p = path or paths.env_file()
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def load_user() -> dict:
    p = paths.user_file()
    if not p.exists():
        raise SetupMissing(f"No profile at {p}. Run:  python3 -m jobpilot setup")
    return json.loads(p.read_text())


def _alt(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted({w.lower() for w in words if w}, key=len, reverse=True))


def build(defaults: dict, user: dict, env: dict) -> dict:
    """Pure function (tested): generic defaults + user answers -> the settings the code uses."""
    role, search = user["role"], user["search"]
    keywords = [k.lower() for k in role["keywords"]]
    kw = _alt(keywords)
    catalog = {k: v for k, v in defaults["region_catalog"].items() if not k.startswith("_")}
    order = [r for r in search["regions"] if r == "remote" or r in catalog]

    # --- regions (priority order) and the searches they imply
    regions = {}
    points = {}
    for rank, r in enumerate(order):
        pts = POINTS_BY_RANK[min(rank, len(POINTS_BY_RANK) - 1)]
        points[r] = pts
        if r != "remote":
            regions[r] = {"points": pts, "patterns": catalog[r]["patterns"]}

    title = role.get("search_title") or role["target_title"]
    queries = []
    for r in order:
        if r == "remote":
            q = {"query": f"{title} remote", "remote_only": True}
            if search.get("home_country"):
                q["country"] = search["home_country"]
            queries.append(q)
        else:
            queries += [{"query": f"{title} in {p['place']}", "country": p["country"], "region": r} for p in catalog[r]["jsearch"]]

    # --- deal-breakers built from the user's answers
    f = dict(defaults["filters"])
    deal = {k: v for k, v in f.get("dealbreakers", {}).items() if not k.startswith("_")}
    if search.get("us_citizen"):
        deal.pop("citizenship / clearance required", None)
    spoken = {l.lower() for l in search.get("languages", [])}
    for lang in f.get("languages", []):
        if lang not in spoken:
            l = re.escape(lang)
            deal[f"{lang.capitalize()} speaker required"] = [
                f"re:{l}[\\s-]*speak", f"re:(fluent|fluency|native|proficien\\w*)\\s+(in\\s+)?{l}\\b",
                f"re:\\b{l}\\s+(language\\s+)?(is\\s+)?(a\\s+)?(must|required|mandatory)"]
    other_regions = [r for r in catalog if r not in order]
    elsewhere = sorted({w for r in other_regions for w in catalog[r]["patterns"] if len(w) > 3})
    if elsewhere:
        e = _alt(elsewhere)
        deal["based outside target countries"] = [
            f"re:\\b(based|located)\\s+in\\s+(the\\s+)?({e})\\b",
            f"re:on-?site\\s+(roles?\\s+)?(available\\s+)?in\\s+({e})\\b", f"re:job location:\\s*({e})\\b"]
    stacks = [s.lower() for s in role.get("exclude_title_stacks", [])]
    with_kw = [s for s in stacks if re.search(rf"\b({kw})", s)]
    without_kw = [s for s in stacks if s not in with_kw]
    pats = []
    if without_kw:
        pats.append(f"re:title:\\b({_alt(without_kw)})\\b(?![^|]*\\b({kw}))")
    if with_kw:
        pats.append(f"re:title:\\b({_alt(with_kw)})\\b")
    if pats:
        deal["other primary stack"] = pats
    deal[f"{keywords[0].capitalize()} is not the main stack"] = [
        f"re:title:\\b({kw})\\s*(is\\s+)?(a\\s+)?(plus|bonus|nice[- ]to[- ]have|preferred)"]

    f.update({
        "title_must_match_any": keywords,
        "related_titles_need_keyword": role.get("related_titles", []),
        "title_exclude_any": f["title_exclude_any"] + [t for t in role.get("exclude_titles", []) if t],
        "title_exclude_unless_keyword": role.get("competing_stacks", []) or ["__none__"],
        "remote_regions_ok": search.get("remote_ok", []) or ["__none__"],
        "regions": regions,
        "dealbreakers": deal,
        "dealbreakers_onsite_only": ["based outside target countries"],
        "salary_floor": search.get("salary_floor"),
        "drop_unspecified_seniority": role.get("seniority") in ("lead", "principal"),
    })

    cfg = {k: v for k, v in defaults.items() if k not in ("filters", "region_catalog", "_why")}
    cfg["filters"] = f
    cfg["region_points"] = points
    cfg["region_order"] = order
    cfg["search_terms"] = role.get("search_terms") or keywords[:1]
    cfg["companies"] = search.get("companies", {})
    cfg["jsearch"] = dict(defaults["jsearch"], queries=queries)
    cfg["user"] = user
    cfg["env"] = env
    return cfg


def load() -> dict:
    defaults = json.loads((paths.REPO / "config.json").read_text())
    return build(defaults, load_user(), load_env())


def load_skills() -> dict:
    """skills.json + the career start from user.json (for 'years of experience')."""
    p = paths.skills_file()
    if not p.exists():
        raise SetupMissing(f"No skills profile at {p}. Run:  python3 -m jobpilot setup --section skills")
    skills = json.loads(p.read_text())
    skills.setdefault("career_start", load_user()["role"].get("career_start", "2020-01"))
    return skills
