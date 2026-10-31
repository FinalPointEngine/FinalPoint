"""ThirdEye's own rule set. Each rule returns (points, reason or None). Pure functions, no I/O.
Score starts at 50 and is clamped to 0-100. A veto forces REJECT whatever the score."""
from __future__ import annotations

DONE = ("won", "lost", "halfwon", "halflost", "void")


def r_edge(p):
    ev = p["ev"]
    if ev > 0.25:
        return -25, "Edge %+.1f%% is too good to be true (likely a stale price or wrong match)" % (ev * 100)
    if ev > 0.15:
        return -10, "Edge %+.1f%% is unusually large: treated with suspicion" % (ev * 100)
    if ev >= 0.06:
        return 20, "Edge %+.1f%%: strong" % (ev * 100)
    if ev >= 0.03:
        return 8, "Edge %+.1f%%: real but modest" % (ev * 100)
    if ev >= 0:
        return -5, "Edge %+.1f%%: not enough value" % (ev * 100)
    return -30, "Edge %+.1f%%: negative, no value" % (ev * 100)


def r_persistence(p):
    n = len(p["hist"])
    if not p["n_checks"]:
        return 0, None
    if n >= 3:
        return 15, "Edge held through %d separate checks" % n
    if n == 2:
        return 8, "Edge held through 2 checks"
    if n == 1:
        return -3, "Edge seen at only 1 check so far"
    return -5, "Edge not confirmed by any stored check"


def r_drift(p):
    h = p["hist"]
    if len(h) < 2:
        return 0, None
    d = h[-1][1] - h[0][1]
    if d >= 0.01:
        return 10, "Fair probability rose %+.1f pts since the first check (sharp money agrees)" % (d * 100)
    if d <= -0.02:
        return -20, "Fair probability fell %+.1f pts since the first check (edge shrinking)" % (d * 100)
    return 0, None


def r_clv(p):
    """Closing-line proxy. NOT used in the live score (before kick-off it equals the edge, so it would count it twice);
    it is only a measurement after the game."""
    c = p.get("close")
    if not c or not p["odds"]:
        return 0, None
    clv = c * (p.get("took") or p["odds"]) - 1.0
    if clv >= 0.02:
        return 10, "Beats the latest Pinnacle price by %+.1f%% (CLV)" % (clv * 100)
    if clv < 0:
        return -15, "Latest Pinnacle price is worse than this bet by %+.1f%% (negative CLV)" % (clv * 100)
    return 0, None


def r_stale(p):
    if p.get("stale"):
        return -15, "Pinnacle price behind this pick looked stale"
    return 0, None


def r_range(p):
    pts, why = 0, []
    if p["odds"] > 4.5:
        pts -= 10
        why.append("odds %.2f are long" % p["odds"])
    if 0 < p["p"] < 0.35:
        pts -= 10
        why.append("win chance only %.0f%%" % (p["p"] * 100))
    if p["p"] >= 0.55:
        pts += 5
        why.append("win chance %.0f%%: solid" % (p["p"] * 100))
    return pts, ("Price profile: " + ", ".join(why)) if why else None


def r_timing(p, now):
    left = p["tip"] - now
    if p["tip"] and left < 0:
        return -30, "Kick-off has passed"
    return 0, None


def r_record(rec):
    """rec = {'n': graded, 'roi': flat-unit ROI} for this sport."""
    if not rec or rec["n"] < 20:
        return 0, None
    if rec["roi"] <= -0.10:
        return -10, "This sport's engine is %+.0f%% over %d graded picks" % (rec["roi"] * 100, rec["n"])
    if rec["roi"] >= 0.10:
        return 5, "This sport's engine is %+.0f%% over %d graded picks" % (rec["roi"] * 100, rec["n"])
    return 0, None


def r_exposure(p, open_same_match, open_sport):
    pts, why = 0, []
    if open_same_match:
        pts -= 10
        why.append("%d other pick(s) on this match are correlated" % open_same_match)
    if open_sport > 12:
        pts -= 5
        why.append("%d open picks in this sport" % open_sport)
    return pts, ("Exposure: " + "; ".join(why)) if why else None


def veto(p):
    if p.get("dup_of"):
        return "Same bet as pick #%s (the engine sent it twice)" % p["dup_of"]
    if p["odds"] and p["odds"] < 1.2:
        return "Odds too short to matter"
    if 0 < p["p"] < 0.25:
        return "Win chance under 25%"
    if p["ev"] > 0.30:
        return "Edge above 30% is almost always a data error"
    return None
