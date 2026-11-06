"""Sends every check of a match, as structured data, to the FinalPoint service. Off unless FP_FEED_URL is set.
FP_FEED_URL = https://<finalpoint>.onrender.com/fp/feed   FP_FEED_TOKEN = shared secret (header X-FP-Token).
Never raises and never slows a check: the data goes into a queue and a background thread posts it (3 tries, so a sleeping
free Render service has time to wake up)."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.request

log = logging.getLogger("fpfeed")
_Q: "queue.Queue" = queue.Queue(maxsize=500)
_T = {"t": None}


def jsonable(x):
    if isinstance(x, dict):
        return {("%g" % k if isinstance(k, float) else str(k)): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if isinstance(x, float):
        return round(x, 5)
    if x is None or isinstance(x, (int, str, bool)):
        return x
    return str(x)


def payload(tag: str, m: dict, chk: dict, fx: dict = None, min_ev: float = 0.04) -> dict:
    keep = ("label", "kind", "t", "status", "age", "stale", "held", "keys", "pmap", "omap", "nxt", "away", "rows", "hint")
    return jsonable(dict(v=1, tag=tag, key=str(m.get("key")), league=m.get("lname") or m.get("league") or "", home=m.get("home"),
                         away=m.get("away"), tip=m.get("tip"), min_ev=min_ev, sent=time.time(),
                         chk={k: chk.get(k) for k in keep if k in chk}, soft=(fx or {}).get("soft"), pin=(fx or {}).get("pin")))


def _post(body: bytes):
    req = urllib.request.Request(os.environ["FP_FEED_URL"], data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-FP-Token": os.environ.get("FP_FEED_TOKEN", "")})
    urllib.request.urlopen(req, timeout=40).read()


def _worker():
    while True:
        body = _Q.get()
        for i in range(3):
            try:
                _post(body)
                break
            except Exception as e:
                if i == 2:
                    log.warning("FinalPoint feed failed: %s", str(e)[:120])
                else:
                    time.sleep(20)


def emit(tag: str, m: dict, chk: dict, fx: dict = None, min_ev: float = 0.04):
    if not os.environ.get("FP_FEED_URL"):
        return
    try:
        body = json.dumps(payload(tag, m, chk, fx, min_ev)).encode()
        if _T["t"] is None:
            _T["t"] = threading.Thread(target=_worker, name="fp-feed", daemon=True)
            _T["t"].start()
        _Q.put_nowait(body)
    except Exception as e:
        log.warning("FinalPoint feed not queued: %s", str(e)[:120])
