"""Job statuses, in workflow order. One source of truth for the CLI and the sheet.

New          found and qualified, not tailored yet
Tailoring    folder prepared, CV being tailored
Ready        CV (and cover letter) finalized, waiting for your review
Approved     you approved it for the apply step
Pending      apply started but not finished ("later")
Failed       the application could not be completed (error on the form, site down...)
Manual action required   you must apply yourself (LinkedIn/Indeed, security check, ...)
Submitted    ONLY after the site's confirmation page, or your explicit confirmation
Interview / Offer / Rejected / Skipped
"""
from __future__ import annotations

STATUSES = ["New", "Tailoring", "Ready", "Approved", "Pending", "Failed", "Manual action required",
            "Submitted", "Interview", "Offer", "Rejected", "Skipped"]

# Statuses only move forward; equal ranks may switch between each other (e.g. Failed -> Pending).
RANK = {"New": 0, "Tailoring": 1, "Ready": 2, "Approved": 3,
        "Pending": 4, "Failed": 4, "Manual action required": 4,
        "Submitted": 5, "Interview": 6, "Offer": 7, "Rejected": 7, "Skipped": 7}

ALIASES = {"Applied": "Submitted"}  # older rows / sheet values

# Jobs the apply step will open.
APPLYABLE = ("Approved", "Pending", "Failed", "Manual action required")
# Jobs that count as "applied before" for the same company.
APPLIED_OR_LATER = ("Submitted", "Interview", "Offer", "Rejected")


def normalize(status: str) -> str:
    return ALIASES.get(status, status)


def can_move(current: str, new: str) -> bool:
    return RANK.get(normalize(new), -1) >= RANK.get(normalize(current), 0)
