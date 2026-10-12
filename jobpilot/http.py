"""Tiny stdlib HTTP helper so the project needs zero pip installs."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) job-pilot/0.1"
TIMEOUT = 25


def get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    return _request(url, headers=headers)


def post_json(url: str, body: Any, headers: dict[str, str] | None = None) -> Any:
    return _request(url, data=body, headers=headers)


def _request(url: str, data: Any = None, headers: dict[str, str] | None = None) -> Any:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    payload = None
    if data is not None:
        payload = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=payload, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else None


HTTPError = urllib.error.HTTPError
URLError = urllib.error.URLError
