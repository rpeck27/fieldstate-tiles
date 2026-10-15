"""HTTP for the tile build: FieldstateBot identity, robots.txt obeyed, one
request per host gap.

Inside sie-engine this is sie.http_client (signed, shared robots cache). In
the public tiles repo, where the engine package is not present, a small
stand-in does the same three things with requests + urllib.robotparser.
"""
from __future__ import annotations

import sys
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

UA = "FieldstateBot/1.0 (+https://fieldstate.org/bot; bots@fieldstate.org)"
HOST_GAP_S = 1.0

_here = Path(__file__).resolve()
for parent in _here.parents:
    if (parent / "sie" / "http_client.py").exists():
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        break

try:  # the engine's own client when we run inside sie-engine
    from sie import http_client as _engine  # type: ignore
except Exception:  # noqa: BLE001 — public repo: stand-in below
    _engine = None


class RobotsDisallowed(RuntimeError):
    pass


_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last: dict[str, float] = {}


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def robots_allowed(url: str) -> bool:
    if _engine is not None:
        return _engine.robots_allowed(url)
    origin = _origin(url)
    if origin not in _robots:
        import requests

        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(origin + "/robots.txt", headers={"User-Agent": UA}, timeout=20)
        except Exception:  # noqa: BLE001 — unreachable robots: be conservative
            _robots[origin] = None
            return False
        if r.status_code >= 500 or r.status_code in (401, 403):
            _robots[origin] = None  # unreachable or forbidden: treat as disallowed
            return False
        rp.parse(r.text.splitlines() if r.status_code < 400 else [])
        _robots[origin] = rp
    rp = _robots[origin]
    return bool(rp and rp.can_fetch("FieldstateBot", url))


def get(url: str, *, params=None, timeout=60):
    if _engine is not None:
        return _engine.get(url, params=params, timeout=timeout)
    import requests

    if not robots_allowed(url):
        raise RobotsDisallowed(url)
    host = urlsplit(url).netloc
    wait = _last.get(host, 0) + HOST_GAP_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": UA})
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
            continue
        finally:
            _last[host] = time.monotonic()
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(10 * (attempt + 1))
            continue
        return r
    return r


if __name__ == "__main__":  # robots check for build.sh: exit 0 allowed, 3 not
    sys.exit(0 if robots_allowed(sys.argv[1]) else 3)
