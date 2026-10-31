"""ThirdEye's own model. It re-prices EVERY selection the engines stored for a match, with its own parameters:

 1. own probability: Pinnacle's fair chance blended with SportyBet's own de-margined chance (TE_PIN_W, default 0.85 Pinnacle),
    so a price that SportyBet itself already shortens counts for less than the engine believes;
 2. own gates: edge band, margin ceiling, odds ceiling, minimum win chance (all TE_* settings, stricter than the engines');
 3. own scoring (rules.py / verdict.py): persistence and drift of that selection across every stored check, CLV proxy, price profile.
Pure functions, no I/O."""
from __future__ import annotations

from thirdeye import cfg, verdict


def describe(key, home, away):
    mkt, line, sel = (key.split("|") + ["", ""])[:3]
    if sel in ("over", "under"):
        return "%s %s" % (sel.title(), line)
    if sel == "draw":
        return "Draw"
    if sel in ("home", "away"):
        team = home if sel == "home" else away
        if line and mkt not in ("1x2", "ml"):
            try:
                v = float(line) if sel == "home" else -float(line)
                return "%s %s" % (team, "0 (draw no bet)" if v == 0 else "%+g" % v)
            except ValueError:
                pass
        return "%s to win" % team
    return "%s %s %s" % (mkt, line, sel)


def own_probs(omap, pmap):
    """key -> (pinnacle p, sporty de-margined p, blended p) for every selection that has both."""
    w = cfg.f("TE_PIN_W", 0.85)
    groups = {}
    for k, v in omap.items():
        mkt, line, _ = (k.split("|") + ["", ""])[:3]
        groups.setdefault((mkt, line), []).append(k)
    out = {}
    for ks in groups.values():
        inv = {k: 1.0 / omap[k][0] for k in ks if omap[k][0] > 1.0}
        tot = sum(inv.values())
        if not tot:
            continue
        for k in ks:
            if k in inv and k in pmap:
                ps = inv[k] / tot
                out[k] = (pmap[k], ps, w * pmap[k] + (1 - w) * ps)
    return out


def _score(m, chk, k, probs, now):
    pp, ps, pt = probs[k]
    odds, margin = chk[-1]["omap"][k]
    ev = pt * odds - 1.0
    hist = [[c["t"], c["pmap"][k]] for c in chk if k in c["pmap"]]
    pick = dict(ev=ev, p=pt, odds=odds, hist=hist, n_checks=len(chk), tip=m["tip"], status="new", stale=m.get("stale", False), close=pp, took=None)
    j = verdict.judge(pick, now)
    return dict(key=k, odds=odds, margin=margin, ev=ev, p=pt, p_pin=pp, p_sporty=ps, label=j["label"], score=j["score"], mult=j["mult"],
                reasons=j["reasons"], desc=describe(k, m["home"], m["away"]), n=len(hist))


def _chk(m):
    return [c for c in m.get("checks", []) if c.get("omap") and c.get("pmap")]


def calls_for(m, now, n=3):
    """ThirdEye's own markets for a match: best selection per market family that passes its own gates, top n by score."""
    chk = _chk(m)
    if not chk or m["tip"] < now:
        return []
    probs = own_probs(chk[-1]["omap"], chk[-1]["pmap"])
    best = {}
    for k in probs:
        odds, margin = chk[-1]["omap"][k]
        ev = probs[k][2] * odds - 1.0
        if not (cfg.f("TE_MIN_EV", 0.03) <= ev <= cfg.f("TE_MAX_EV", 0.15)) or margin > cfg.f("TE_MAX_MARGIN", 0.10):
            continue
        if odds > cfg.f("TE_MAX_ODDS", 5.0) or probs[k][2] < cfg.f("TE_MIN_P", 0.35) or k.split("|")[2] == "draw":
            continue
        c = _score(m, chk, k, probs, now)
        fam = k.split("|")[0]
        if fam not in best or (c["score"], c["ev"]) > (best[fam]["score"], best[fam]["ev"]):
            best[fam] = c
    out = [c for c in best.values() if c["score"] >= cfg.reduce_()]
    return sorted(out, key=lambda c: (-c["score"], -c["ev"]))[:n]


def call_for(m, now):
    cs = calls_for(m, now, 1)
    return cs[0] if cs else None


def review(m, key, now):
    """ThirdEye's own score for ONE exact market (used to review the engine's pick); None when it has no price data for it."""
    chk = _chk(m)
    if not chk or not key:
        return None
    probs = own_probs(chk[-1]["omap"], chk[-1]["pmap"])
    return _score(m, chk, key, probs, now) if key in probs else None
