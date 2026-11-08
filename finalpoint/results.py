"""After the match: find the final score, grade the 3 starred picks (Won / Lost with a plain-English reason), add the MATCH ENDED block to the card
and update FinalPoint's own record of how each kind of pick (VALIDATED / POSSIBLE / WATCH) is doing.
Scores come from the free feeds ThirdEye already uses (thirdeye/sources.py); handball and volleyball often have none, so /result in the group
(or FinalPoint.manual_result) supplies the score."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("finalpoint.results")

SPORT = {"FB": "football", "TN": "tennis", "IH": "hockey", "AF": "afootball", "HB": "handball", "VB": "volleyball", "BB": "basketball"}
EARLIEST = 2.25 * 3600          # do not look for a result earlier than this after kick-off
LATEST = 40 * 3600              # give up after this
RETRY = 600
TIER = {0: "VALIDATED", 1: "POSSIBLE", 2: "POSSIBLE", 3: "WATCH"}
SHOW = {"won": "Won", "lost": "Lost", "void": "Void (stake returned)", "halfwon": "Half won", "halflost": "Half lost"}


# ---------------------------------------------------------------- finding the score
_cache = {}


def _day_rows(sport, date, getter):
    k = (sport, date)
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    rows = getter(sport, date)
    _cache[k] = (time.time(), rows)
    return rows


def _same(a, b):
    """Same team: the shared matcher, or one name's words all appear in the other ('Seattle' / 'Seattle Kraken')."""
    from football import names
    if names.same_team(a, b):
        return True
    ta, tb = set(names.tokens(a)), set(names.tokens(b))
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return bool(small) and small <= big and any(len(w) >= 4 for w in small) and names.markers(a) == names.markers(b)


def find_score(tag, home, away, tip, getter=None):
    """(home score, away score) of the finished game or None. Same two teams, started within 8 hours of the expected time."""
    from football import names
    if getter is None:
        from thirdeye.sources import fetch_day as getter
    sport = SPORT.get(tag)
    if not sport or not tip:
        return None
    d0 = datetime.fromtimestamp(tip, tz=timezone.utc)
    for d in (d0, d0 + timedelta(days=1)):
        try:
            rows = _day_rows(sport, d.strftime("%Y-%m-%d"), getter)
        except Exception as e:
            log.info("no result source for %s: %s", sport, str(e)[:80])
            continue
        for t, _lg, h, a, hg, ag in rows:
            if abs(t - tip) <= 8 * 3600 and _same(h, home) and _same(a, away):
                return int(hg), int(ag)
    return None


# ---------------------------------------------------------------- grading
def _simple(mkt, line, sel, hg, ag):
    """Basketball (no sport model in ThirdEye): whole and half lines only. -> (status, profit per 1 unit at odds 2.0 is added by caller)."""
    if mkt == "ml":
        res = "home" if hg > ag else "away" if ag > hg else None
        return "void" if res is None else ("won" if res == sel else "lost")
    if mkt in ("ah", "total") and line is not None:
        if mkt == "total":
            diff = (hg + ag - line) if sel == "over" else (line - hg - ag)
        else:
            diff = (hg + line - ag) if sel == "home" else (ag - line - hg)
        return "won" if diff > 1e-9 else "lost" if diff < -1e-9 else "void"
    return None


def settle(tag, pick, hg, ag):
    """-> (status, ret) or None when it cannot be graded safely (a market the score does not decide)."""
    key, odds = pick["pkey"], pick["odds"]
    sport = SPORT.get(tag)
    if tag == "BB":
        st = _simple(pick["mkt"], pick.get("line"), pick["sel"], hg, ag)
        if st is None:
            return None
        return st, ((odds - 1.0) if st == "won" else -1.0 if st == "lost" else 0.0)
    from thirdeye import ledger
    return ledger.settle(sport, key, odds, hg, ag)


def _n(x):
    return ("%g" % x)


def reason(tag, pick, status, hg, ag, home, away):
    """One plain-English sentence on why the pick won or lost."""
    mkt, line, sel = pick["mkt"], pick.get("line"), pick["sel"]
    tot, win = hg + ag, (home if hg > ag else away if ag > hg else None)
    final = "%s %d-%d %s" % (home, hg, ag, away)
    if mkt in ("1x2", "ml"):
        if sel == "draw":
            return "The match ended level (%d-%d)." % (hg, ag) if status == "won" else "It did not end level: %s won %d-%d." % (win, max(hg, ag), min(hg, ag))
        team = home if sel == "home" else away
        if tag == "TN":                                    # the free feed gives sets (or just a win), so no score is quoted
            return "%s won the match." % (home if hg > ag else away) if (home if hg > ag else away) == team else "%s won the match, so %s did not." % (home if hg > ag else away, team)
        if win is None:
            return "The match ended level (%d-%d), so %s did not win." % (hg, ag, team)
        return "%s won %d-%d." % (win, max(hg, ag), min(hg, ag)) if win == team else "%s won %d-%d, so %s did not." % (win, max(hg, ag), min(hg, ag), team)
    if line is not None and abs(line * 2 - round(line * 2)) < 1e-9:
        if mkt == "ah":
            diff = (hg + line - ag) if sel == "home" else (ag - line - hg)
            team = home if sel == "home" else away
            tl = line if sel == "home" else -line
            who = "%s won by %d" % (win, abs(hg - ag)) if win else "the match was level"
            if diff > 1e-9:
                return "Final %d-%d: %s. With %s %+g the pick had %s points to spare." % (hg, ag, who, team, tl, _n(diff))
            if diff < -1e-9:
                return "Final %d-%d: %s. With %s %+g the pick missed by %s." % (hg, ag, who, team, tl, _n(-diff))
            return "Final %d-%d: %s. It landed exactly on the line, so the stake is returned." % (hg, ag, who)
        if mkt == "total":
            diff = (tot - line) if sel == "over" else (line - tot)
            word = "over" if tot > line else "under"
            unit = {"FB": "goals", "IH": "goals", "HB": "goals", "TN": "games"}.get(tag, "points")
            if abs(diff) < 1e-9:
                return "%d %s in total, exactly on the %g line, so the stake is returned." % (tot, unit, line)
            return "%d %s in total, %s %s the %g line." % (tot, unit, _n(abs(tot - line)), word, line)
    return "Final score %d-%d. This market follows the sport's own rule for %s." % (hg, ag, "quarter lines" if line is not None else "this bet")


def lesson(picks, stats):
    """A short rule-based lesson: what happened to each tier of pick here, and where the running record now stands."""
    by = {}
    for g in picks:
        by.setdefault(g["tier"], []).append(g["status"])
    parts = []
    for t in (0, 1, 2, 3):
        sts = by.get(t)
        if not sts:
            continue
        w = sum(1 for x in sts if x in ("won", "halfwon"))
        n = sum(1 for x in sts if x in ("won", "lost", "halfwon", "halflost"))
        if n:
            parts.append("%s %s" % (TIER[t], "won" if w == n else "lost" if w == 0 else "split %d of %d" % (w, n)))
    head = ("This match: " + ", ".join(parts) + ".") if parts else "No pick could be graded on this score."
    rec = []
    for name in ("VALIDATED", "POSSIBLE"):                 # WATCH picks are only the closest to value, never a recommendation: tracked, not shown
        s = stats.get(name) or {}
        n = s.get("w", 0) + s.get("l", 0)
        if n:
            rec.append("%s %dW-%dL (%+.1f units)" % (name, s.get("w", 0), s.get("l", 0), s.get("u", 0.0)))
    tail = " Record so far: " + ", ".join(rec) + "." if rec else ""
    v = stats.get("VALIDATED") or {}
    note = " Rules change only after about 30 graded VALIDATED picks." if v.get("w", 0) + v.get("l", 0) < 30 else ""
    return head + tail + note


def end_block(tag, rec, hg, ag, stats):
    """Grade the picks of the last check. -> (end dict, picks graded). Mutates stats (the running record)."""
    home, away = rec.get("home") or "", rec.get("away") or ""
    graded = []
    for pk in rec.get("picks") or []:
        r = settle(tag, pk, hg, ag)
        if r is None:
            graded.append(dict(d=pk["d"], res="Not graded", why="This market is not decided by the final score the free feed gives (sets or games are missing).",
                               tier=pk.get("tier", 3), status="ungraded"))
            continue
        st, ret = r
        name = TIER.get(pk.get("tier", 3), "WATCH")
        s = stats.setdefault(name, dict(w=0, l=0, v=0, u=0.0))
        if st in ("won", "halfwon"):
            s["w"] += 1
        elif st in ("lost", "halflost"):
            s["l"] += 1
        else:
            s["v"] += 1
        s["u"] = round(s["u"] + ret, 3)
        graded.append(dict(d="%s [%s]" % (pk["d"], name), res=SHOW.get(st, st), why=reason(tag, pk, st, hg, ag, home, away), tier=pk.get("tier", 3),
                           status=st, ret=round(ret, 3), pkey=pk["pkey"]))
    score = ("%s %d-%d %s" % (home, hg, ag, away)) if tag != "TN" else ("%s beat %s" % ((home, away) if hg > ag else (away, home)))
    end = dict(score=score, hg=hg, ag=ag, picks=graded, lesson=lesson(graded, stats), at=int(time.time()))
    if not graded:
        end["note"] = "FinalPoint had no priced market to grade for this match."
    return end, graded
