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


def call_for(m, now):
    """ThirdEye's single best call for a match, or None. m = canonical match with 'checks' (t, pmap, omap)."""
    chk = [c for c in m.get("checks", []) if c.get("omap") and c.get("pmap")]
    if not chk or m["tip"] < now:
        return None
    last = chk[-1]
    probs = own_probs(last["omap"], last["pmap"])
    best = None
    for k, (pp, ps, pt) in probs.items():
        odds, margin = last["omap"][k]
        ev = pt * odds - 1.0
        if not (cfg.f("TE_MIN_EV", 0.03) <= ev <= cfg.f("TE_MAX_EV", 0.15)) or margin > cfg.f("TE_MAX_MARGIN", 0.10):
            continue
        if odds > cfg.f("TE_MAX_ODDS", 5.0) or pt < cfg.f("TE_MIN_P", 0.35) or k.split("|")[2] == "draw":
            continue
        hist = [[c["t"], c["pmap"][k]] for c in chk if k in c["pmap"]]
        pick = dict(ev=ev, p=pt, odds=odds, hist=hist, n_checks=len(chk), tip=m["tip"], status="new", stale=m.get("stale", False),
                    close=pp, took=None)
        j = verdict.judge(pick, now)
        cand = dict(key=k, odds=odds, margin=margin, ev=ev, p=pt, p_pin=pp, p_sporty=ps, label=j["label"], score=j["score"],
                    mult=j["mult"], reasons=j["reasons"], desc=describe(k, m["home"], m["away"]), n=len(hist))
        if best is None or (cand["score"], cand["ev"]) > (best["score"], best["ev"]):
            best = cand
    return best
