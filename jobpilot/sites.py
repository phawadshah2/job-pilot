"""Where a job is applied to, decided from its link. One source of truth for sources,
filters, the apply step and the sheet's "Apply via" column.

  form       the employer's own application form on a no-account ATS: pre-filled for you
  company    the employer's own careers site (unknown host): pre-filled when a form is found
  board      a remote job board page that sends you on to the employer
  account    a job platform you sign in to once (LinkedIn, Bayt, Wellfound...): you apply in
             your own browser, never automated
  portal     an enterprise ATS that needs an account per company (Workday, SuccessFactors,
             Taleo...): you apply in your own browser
  aggregator re-posting sites that hide the employer: dropped
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# (kind, display name, host pattern). First match wins; checked against the host only.
SITES: list[tuple[str, str, str]] = [
    # no-account ATS forms
    ("form", "Greenhouse", r"greenhouse\.io$"),
    ("form", "Lever", r"(^|\.)lever\.co$"),
    ("form", "Ashby", r"ashbyhq\.com$"),
    ("form", "Workable", r"workable\.com$"),
    ("form", "SmartRecruiters", r"smartrecruiters\.com$"),
    ("form", "Recruitee", r"recruitee\.com$"),
    ("form", "Personio", r"personio\.(de|com)$"),
    ("form", "Breezy", r"breezy\.hr$"),
    ("form", "Teamtailor", r"teamtailor\.com$"),
    ("form", "BambooHR", r"bamboohr\.com$"),
    ("form", "JazzHR", r"applytojob\.com$"),
    ("form", "Zoho Recruit", r"zohorecruit\.(com|eu|in)$"),
    # platforms you sign in to once
    ("account", "LinkedIn", r"(^|\.)linkedin\.com$|^lnkd\.in$"),
    ("account", "Indeed", r"(^|\.)indeed\.(com|ae|co\.uk|com\.au)$"),
    ("account", "Bayt", r"(^|\.)bayt\.com$"),
    ("account", "NaukriGulf", r"(^|\.)naukrigulf\.com$"),
    ("account", "GulfTalent", r"(^|\.)gulftalent\.com$"),
    ("account", "foundit Gulf", r"(^|\.)(founditgulf\.com|foundit\.ae|monstergulf\.com)$"),
    ("account", "Wellfound", r"(^|\.)(wellfound\.com|angel\.co)$"),
    ("account", "Welcome to the Jungle", r"(^|\.)(welcometothejungle\.com|otta\.com)$"),
    ("account", "Arc", r"(^|\.)arc\.dev$"),
    ("account", "Turing", r"(^|\.)turing\.com$"),
    ("account", "Glassdoor", r"(^|\.)glassdoor\.[a-z.]+$"),
    ("account", "Dice", r"(^|\.)dice\.com$"),
    ("account", "Reed", r"(^|\.)reed\.co\.uk$"),
    ("account", "SEEK", r"(^|\.)seek\.com(\.au)?$"),
    # one account per company
    ("portal", "Workday", r"myworkdayjobs\.com$|myworkdaysite\.com$|workday\.com$"),
    ("portal", "SuccessFactors", r"successfactors\.(com|eu)$|^jobs\.sap\.com$|sapsf\.(com|eu)$"),
    ("portal", "Taleo", r"taleo\.net$"),
    ("portal", "Oracle Cloud", r"oraclecloud\.com$"),
    ("portal", "iCIMS", r"icims\.com$"),
    # remote boards that pass you on to the employer
    ("board", "Himalayas", r"(^|\.)himalayas\.app$"),
    ("board", "Remotive", r"(^|\.)remotive\.com$"),
    ("board", "RemoteOK", r"(^|\.)remoteok\.(com|io)$"),
    ("board", "Jobicy", r"(^|\.)jobicy\.com$"),
    ("board", "We Work Remotely", r"(^|\.)weworkremotely\.com$"),
    # re-posting sites: dropped
    ("aggregator", "aggregator", r"(^|\.)(jooble\.org|jobleads\.com|learn4good\.com|jobilize\.com|bebee\.com|"
                                 r"trabajo\.org|jobrapido\.com|talent\.com|adzuna\.[a-z.]+|ai-search\.io|"
                                 r"mzadqatar\.com|kuwaitjobshere\.com|upwork\.com|ziprecruiter\.com|"
                                 r"simplyhired\.[a-z.]+|careerjet\.[a-z.]+|jobsora\.com|whatjobs\.com|jora\.com|"
                                 r"lensa\.com|jobgether\.com|remoterocketship\.com|jobright\.ai|"
                                 r"dubizzle\.com|naukri\.com|recruit\.net|jobtoday\.com|"
                                 r"expatriates\.com|grabjobs\.co|jobzmall\.com|tealhq\.com|"
                                 r"jobsinkuwait\.com|gulfjobs\.[a-z.]+|bossjob\.[a-z.]+|uaejobs\.[a-z.]+|"
                                 r"khaleejtimes\.com|jobs\.ae|careers24\.com|drjobpro\.com|hirelateral\.com|"
                                 r"clickajobs\.com|jobisite\.com|virtualvocations\.com|flexjobs\.com|jaabz\.com|jobaaj\.com)$"),
]
_COMPILED = [(k, n, re.compile(p, re.I)) for k, n, p in SITES]

# Links in alert emails go through click trackers; these are followed to the real job page.
TRACKERS = re.compile(r"(^|\.)(sendgrid\.net|links\.wellfound\.com|sendibt\d*\.com|mailchimp\.com|"
                      r"list-manage\.com|hubspotlinks\.com|mandrillapp\.com|click\.[a-z0-9-]+\.[a-z]+|"
                      r"url\d+\.[a-z0-9-]+\.[a-z]+|r\.[a-z0-9.-]+|"
                      # job platforms' own click trackers
                      r"email\.s\.seek\.com\.au|click\.email\.seek\.com\.au|clicks\.reed\.co\.uk|"
                      r"elinks\.dice\.com|cts\.indeed\.com|click\.[a-z0-9-]+\.[a-z0-9-]+\.[a-z.]+)$", re.I)


def host(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower().removeprefix("www.")


def classify(url: str) -> tuple[str, str]:
    """(kind, name) for a link. Unknown hosts are the employer's own site."""
    h = host(url)
    if not h:
        return "company", ""
    for kind, name, rx in _COMPILED:
        if rx.search(h):
            return kind, name
    return "company", h


def apply_link(job_url: str, apply_url: str = "") -> str:
    return apply_url or job_url


def apply_via(job_url: str, apply_url: str = "") -> str:
    """Human label for the sheet: where you will actually apply."""
    kind, name = classify(apply_link(job_url, apply_url))
    return {
        "form": f"{name} form (no account)",
        "company": "Company site",
        "board": f"{name} → company",
        "account": f"{name} (your account)",
        "portal": f"{name} (needs account)",
        "aggregator": "Aggregator",
    }[kind]


def is_aggregator(url: str) -> bool:
    return classify(url)[0] == "aggregator"


def open_only(url: str) -> bool:
    """Never automated: you apply in your own browser."""
    return classify(url)[0] in ("account", "portal")


def rank(url: str) -> int:
    """Best place to apply first (lower is better). Aggregators last."""
    return {"form": 0, "company": 1, "board": 2, "account": 3, "portal": 4, "aggregator": 9}[classify(url)[0]]
