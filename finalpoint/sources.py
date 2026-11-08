"""A second free source of final scores for FinalPoint, and a probe that shows which source works from where the service runs.
ThirdEye's feeds (SofaScore first, ESPN second) stay the first choice; this one is asked only when they found nothing.
Every call is wrapped by the caller: a blocked or changed feed just means 'no result found', never an error in FinalPoint."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

TSDB_SPORT = {"football": "Soccer", "basketball": "Basketball", "tennis": "Tennis", "hockey": "Ice_Hockey", "afootball": "American_Football",
              "handball": "Handball", "volleyball": "Volleyball"}
FINISHED = ("finished", "ft", "aet", "ended", "final", "match finished", "after over time", "after overtime", "after extra time", "ap", "aot", "pen")
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def tsdb_rows(sport, date, getter=None):
    """Finished games of one sport on one date -> [[t, league, home, away, hg, ag]] (same shape as thirdeye.sources.fetch_day)."""
    name = TSDB_SPORT.get(sport)
    if not name or sport == "tennis":                          # tennis needs sets and games: this source gives no reliable score for them
        return []
    key = os.environ.get("FP_TSDB_KEY", "3")
    js = (getter or _get)("https://www.thesportsdb.com/api/v1/json/%s/eventsday.php?d=%s&s=%s" % (key, date, name))
    rows = []
    for e in (js or {}).get("events") or []:
        try:
            if str(e.get("strStatus") or "").strip().lower() not in FINISHED:
                continue
            hs, as_ = e.get("intHomeScore"), e.get("intAwayScore")
            if hs in (None, "") or as_ in (None, ""):
                continue
            ts = e.get("strTimestamp") or ("%sT%s" % (e["dateEvent"], e.get("strTime") or "00:00:00"))
            t = int(datetime.fromisoformat(str(ts).replace("Z", "")[:19]).replace(tzinfo=timezone.utc).timestamp())
            rows.append([t, e.get("strLeague") or "", e["strHomeTeam"], e["strAwayTeam"], int(hs), int(as_)])
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def probe(day=None, getter=None, pause=2.0):
    """One line per sport: how many finished games each source returns for a day, from THIS machine. For the /sources command."""
    from thirdeye import sources as ts
    get = getter or ts.get_json
    day = day or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    out = ["<b>RESULT SOURCES</b> · finished games on %s, as seen from this service" % day]
    for sport in ("football", "basketball", "tennis", "hockey", "afootball", "handball", "volleyball"):
        bits = []
        try:
            n = len(ts.parse_sofa(get("https://api.sofascore.com/api/v1/sport/%s/scheduled-events/%s" % (ts.SLUG[sport], day))))
            bits.append("SofaScore %d" % n)
        except Exception as e:
            bits.append("SofaScore blocked" if any(c in str(e) for c in ("403", "429")) else "SofaScore failed")
        paths = ts.ESPN.get(sport, [])
        if paths:
            try:
                n = 0
                for p in paths:
                    js = get("https://site.api.espn.com/apis/site/v2/sports/%s/scoreboard?dates=%s&limit=1000" % (p, day.replace("-", "")))
                    n += len(ts.parse_espn_tennis(js) if sport == "tennis" else ts.parse_espn(js))
                bits.append("ESPN %d" % n)
            except Exception as e:
                bits.append("ESPN blocked" if any(c in str(e) for c in ("403", "429")) else "ESPN failed")
        else:
            bits.append("ESPN none")
        if sport == "tennis":
            bits.append("TheSportsDB not used")
        else:
            try:
                bits.append("TheSportsDB %d" % len(tsdb_rows(sport, day, get)))
            except Exception as e:
                bits.append("TheSportsDB blocked" if any(c in str(e) for c in ("403", "429")) else "TheSportsDB failed")
            if pause:
                time.sleep(pause)
        out.append("%s: %s" % (sport, " · ".join(bits)))
    out.append("A sport where every source says blocked or 0 can only be graded with /result.")
    return "\n".join(out)
