"""Free result feeds, no key needed. SofaScore (all 7 sports, one call per sport per day) first, ESPN as a fallback for the US leagues.
Every fetch is wrapped by the caller: a blocked or changed feed just means ThirdEye has no stats for that sport (the price checks still run)."""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone

SLUG = {"football": "football", "basketball": "basketball", "tennis": "tennis", "hockey": "ice-hockey", "afootball": "american-football",
        "handball": "handball", "volleyball": "volleyball"}
ESPN = {"basketball": ["basketball/nba"], "hockey": ["hockey/nhl"], "afootball": ["football/nfl"], "football": ["soccer/all"],
        "tennis": ["tennis/atp", "tennis/wta"]}
_blocked = {"until": 0.0}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json", "Referer": "https://www.sofascore.com/"}


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def parse_sofa(js):
    rows = []
    for e in (js or {}).get("events", []):
        try:
            if (e.get("status") or {}).get("type") != "finished":
                continue
            hs, as_ = (e.get("homeScore") or {}).get("current"), (e.get("awayScore") or {}).get("current")
            if hs is None or as_ is None:
                continue
            rows.append([int(e.get("startTimestamp") or 0), (e.get("tournament") or {}).get("name", ""), e["homeTeam"]["name"], e["awayTeam"]["name"],
                         int(hs), int(as_)])
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def parse_espn(js):
    rows = []
    for e in (js or {}).get("events", []):
        try:
            comp = e["competitions"][0]
            if not (comp.get("status") or e.get("status") or {}).get("type", {}).get("completed"):
                continue
            side = {c["homeAway"]: c for c in comp["competitors"]}
            t = int(datetime.fromisoformat(e["date"].replace("Z", "+00:00")).timestamp())
            rows.append([t, "", side["home"]["team"]["displayName"], side["away"]["team"]["displayName"], int(float(side["home"]["score"])),
                         int(float(side["away"]["score"]))])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return rows


def parse_espn_tennis(js):
    """ESPN tennis: tournaments -> groupings -> matches. Sets are not given per player here, so a win is 1-0 (a rating from wins and losses)."""
    rows = []
    for e in (js or {}).get("events", []):
        for gr in e.get("groupings", []) or []:
            for c in gr.get("competitions", []) or []:
                try:
                    if not (c.get("status") or {}).get("type", {}).get("completed"):
                        continue
                    pl = sorted([x for x in c["competitors"] if x.get("athlete")], key=lambda x: x.get("order", 0))
                    if len(pl) != 2:
                        continue                                  # doubles or incomplete
                    t = int(datetime.fromisoformat((c.get("date") or e["date"]).replace("Z", "+00:00")).timestamp())
                    w0 = bool(pl[0].get("winner"))
                    rows.append([t, e.get("name", ""), pl[0]["athlete"]["displayName"], pl[1]["athlete"]["displayName"], 1 if w0 else 0, 0 if w0 else 1])
                except (KeyError, TypeError, ValueError):
                    continue
    return rows


def fetch_day(sport, date, getter=get_json):
    """Finished games of one sport on one UTC date -> [[t, league, home, away, hg, ag]]. Raises when every source failed."""
    err = None
    slug = SLUG.get(sport)
    if slug and time.time() >= _blocked["until"]:
        try:
            return parse_sofa(getter("https://api.sofascore.com/api/v1/sport/%s/scheduled-events/%s" % (slug, date)))
        except Exception as e:                      # blocked, changed, offline: try the fallback
            err = e
            if "403" in str(e) or "429" in str(e):
                _blocked["until"] = time.time() + 6 * 3600          # do not keep knocking on a door that is shut
    rows, ok = [], False
    for path in ESPN.get(sport, []):
        try:
            js = getter("https://site.api.espn.com/apis/site/v2/sports/%s/scoreboard?dates=%s&limit=1000" % (path, date.replace("-", "")))
            rows += parse_espn_tennis(js) if sport == "tennis" else parse_espn(js)
            ok = True
        except Exception as e:
            err = e
    if ok:
        return rows
    raise RuntimeError("no result source worked for %s: %s" % (sport, str(err)[:80]))
