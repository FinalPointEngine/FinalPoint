"""FinalPoint's judgement: value, stability, fishy levels, shadow calculators. Pure functions on plain data."""
from __future__ import annotations

import os


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def edge(odds: float, p: float) -> float:
    """Value of a bet: what SportyBet pays against the fair chance. (SportyBet 1.41, fair 1.49 -> p=0.671 -> -5.4%.)"""
    return odds * p - 1.0


MAX_EDGE = lambda: _f("FP_MAX_EDGE", 0.15)       # a bigger "edge" is far more likely a stale price or a different market
FRESH_MIN = lambda: _f("FP_FRESH_MIN", 10)       # Pinnacle price older than this: the edge is stale
MODEL_MULT = lambda: _f("FP_MODEL_MULT", 2.0)    # a line priced by the ladder (not by Pinnacle) needs this times the edge


def describe(mkt, line, sel, home, away):
    if mkt in ("1x2", "ml"):
        return {"home": "%s to win" % home, "away": "%s to win" % away, "draw": "Draw"}.get(sel, sel)
    if mkt in ("ah", "sh"):
        name = "handicap" if mkt == "ah" else "set handicap"
        if line is None:
            return "%s %s" % (name, sel)
        return "%s %+g" % (home, line) if sel == "home" else "%s %+g" % (away, -line)
    if mkt == "total":
        return "%s %g" % (sel.title(), line)
    if mkt == "es":
        return "Exact sets: %s" % ("2 sets" if sel == "s2" else "3 sets")
    if mkt == "cs":
        return "Correct score (sets) %s" % sel
    return "%s %s %s" % (mkt, "" if line is None else "%g" % line, sel)


def fishy(streak: int, since_first: bool, late: bool, coverage: float, normal_for_league: bool):
    """-> (level 0..3, label). streak = checks in a row without a Pinnacle price (this one included).
    A league where Pinnacle is rarely there is NOT fishy: only a price that was there and went missing counts."""
    if streak == 0:
        return 0, "0 Normal"
    if normal_for_league:
        return 0, "0 No Pinnacle price, normal for this league (%d%% of checks)" % round(100 * coverage)
    if streak >= 3 or (since_first and late):
        return 3, "3 FULL FISHY - TREASURE HUNT"
    if streak == 2:
        return 2, "2 FISHY ALERT - HUNT MODE ON"
    return 1, "1 WARNING"


def verdict(cands: list, hist: dict, age, thr: float, play: bool):
    """cands: rows with enough edge (best first). hist: pkey -> [edge at earlier checks]. -> (verdict, best row or None, notes).
    Rows priced by Pinnacle itself are judged first; a line priced only by the ladder is never more than a LEAN."""
    fresh = age is None or age <= FRESH_MIN()
    lean = None
    for c in [x for x in cands if x["exact"]] + [x for x in cands if not x["exact"]]:
        prev = (hist.get(c["pkey"]) or [None])[-1]
        stable = prev is not None and prev >= thr
        if not c["exact"]:
            note = "model-priced line (Pinnacle does not list it): LEAN at most"
        elif c["edge"] > MAX_EDGE():
            note = "edge above %.0f%%: check the market is really the same on both books" % (100 * MAX_EDGE())
        elif not fresh:
            note = "Pinnacle price is %.0f min old" % age
        elif stable or c.get("toward", False):
            return "PICK", c, ["holds on 2 checks" if stable else "Pinnacle moved toward it"]
        else:
            note = "first check with this edge"
        if lean is None:
            lean = (c, [note])
    if lean:
        return "LEAN", lean[0], lean[1]
    return "NO BET", None, []


def shadow(prev: dict, cur: dict):
    """The calculators as first described, only logged (never used) until graded. prev / cur: {'ml': {home, away p}, 'spread': (line, p home),
    'total': (line, p under)} of two checks. -> list of short flags."""
    out = []
    try:
        if cur.get("ml") and cur.get("spread"):
            fav = "home" if cur["ml"]["home"] >= cur["ml"]["away"] else "away"
            pf = cur["ml"][fav]
            cover = cur["spread"][1] if fav == "home" else 1 - cur["spread"][1]
            if 100 * (pf - cover) > 15:
                out.append("C1 gap %.0f pts (underdog spread)" % (100 * (pf - cover)))
        if prev and prev.get("total") and cur.get("total"):
            lm = cur["total"][0] - prev["total"][0]
            pm = cur["total"][1] - prev["total"][1]
            if lm <= -2.0 and pm <= 0.05:
                out.append("C2 trap: line %+g, Under %+.0f%%" % (lm, 100 * pm))
        if prev and prev.get("spread") and cur.get("spread") and prev.get("ml") and cur.get("ml"):
            sm = cur["spread"][0] - prev["spread"][0]
            mp = abs(cur["ml"]["home"] - prev["ml"]["home"])
            if abs(sm) >= 2.0 and mp <= 0.03:
                out.append("C3 trap: spread %+g, ML %+.0f%%" % (sm, 100 * mp))
    except Exception:
        pass
    return out
