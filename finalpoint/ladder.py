"""The market ladder: from Pinnacle's own prices (moneyline, handicap, total) estimate how likely every margin and every total
is, then price EVERY line SportyBet offers, also the lines Pinnacle does not list. Pure maths, no opinions.

Goal sports (football, ice hockey): two Poisson scoring rates fitted to Pinnacle.  Points sports (American football, handball):
a normal margin and a normal total fitted to Pinnacle.  Volleyball, tennis and basketball: no ladder (only exact lines)."""
from __future__ import annotations

import math
import os
from statistics import NormalDist

from core.devig import devig

N = NormalDist()
GRID = 16
SD = {"AF": (13.5, 13.0), "HB": (5.5, 6.0)}        # (margin SD, total SD) of the normal sports


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def fair_table(mk: dict) -> dict:
    """{(market, line or None): {selection: fair chance}} from one book's complete markets (feed json form)."""
    out = {}
    for k, v in (mk or {}).items():
        if not isinstance(v, dict) or not v:
            continue
        first = next(iter(v.values()))
        flat = not isinstance(first, dict)
        groups = [(None, v)] if flat else [(_num(ln), d) for ln, d in v.items()]
        for ln, d in groups:
            try:
                sels = list(d)
                ps = devig([float(d[s]) for s in sels])
                out[(k, ln)] = dict(zip(sels, ps))
            except Exception:
                continue
    return out


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- distributions
def _pois(lam):
    p = [math.exp(-lam)]
    for k in range(1, GRID):
        p.append(p[-1] * lam / k)
    return p


def poisson_dists(lh, la, ot_tie):
    """(margin pmf, total pmf). ot_tie=True: a level score after 60 minutes is settled in overtime (one more goal, half each)."""
    ph, pa = _pois(lh), _pois(la)
    mg, tt = {}, {}
    for h in range(GRID):
        for a in range(GRID):
            p = ph[h] * pa[a]
            d, t = h - a, h + a
            if d == 0 and ot_tie:
                mg[1] = mg.get(1, 0) + p / 2
                mg[-1] = mg.get(-1, 0) + p / 2
                tt[t + 1] = tt.get(t + 1, 0) + p
            else:
                mg[d] = mg.get(d, 0) + p
                tt[t] = tt.get(t, 0) + p
    return mg, tt


def normal_dists(mu, sm, nu, st):
    mg = {k: N.cdf((k + .5 - mu) / sm) - N.cdf((k - .5 - mu) / sm) for k in range(-90, 91)}
    tt = {k: N.cdf((k + .5 - nu) / st) - N.cdf((k - .5 - nu) / st) for k in range(0, 400)}
    return mg, tt


# ---------------------------------------------------------------- probabilities of one line
def p_ah(mg: dict, line: float, side: str):
    """Chance the side covers a HOME-line handicap, a push removed (the way a two-way price is read). None for quarter lines."""
    if abs(line * 2 - round(line * 2)) > 1e-9:
        return None
    win = sum(p for d, p in mg.items() if d + line > 1e-9)
    lose = sum(p for d, p in mg.items() if d + line < -1e-9)
    if win + lose <= 0:
        return None
    ph = win / (win + lose)
    return ph if side == "home" else 1 - ph


def p_total(tt: dict, line: float, side: str):
    if abs(line * 2 - round(line * 2)) > 1e-9:
        return None
    over = sum(p for t, p in tt.items() if t > line + 1e-9)
    under = sum(p for t, p in tt.items() if t < line - 1e-9)
    if over + under <= 0:
        return None
    po = over / (over + under)
    return po if side == "over" else 1 - po


def p_side(mg: dict):
    """(home win, draw, away win) of the final margin."""
    h = sum(p for d, p in mg.items() if d > 0)
    a = sum(p for d, p in mg.items() if d < 0)
    return h, 1 - h - a, a


# ---------------------------------------------------------------- fitting
def _main(fair, mkt):
    """The (line, {sel: p}) of a market whose two sides are closest to even."""
    best = None
    for (k, ln), d in fair.items():
        if k != mkt or ln is None:
            continue
        sels = list(d.values())
        gap = abs(sels[0] - 0.5)
        if best is None or gap < best[0]:
            best = (gap, ln, d)
    return (best[1], best[2]) if best else (None, None)


def _bisect(fn, lo, hi, target, steps=26):
    flo = fn(lo) - target
    for _ in range(steps):
        mid = (lo + hi) / 2
        fm = fn(mid) - target
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def fit(tag: str, pin_fair: dict):
    """-> (mg, tt, error) or None. error = largest gap (chance points as 0..1) between the fit and Pinnacle's own lines."""
    if tag in ("FB", "IH"):
        return _fit_poisson(tag, pin_fair)
    if tag in ("AF", "HB"):
        return _fit_normal(tag, pin_fair)
    return None


def _side_target(pin_fair):
    x = pin_fair.get(("1x2", None))
    if x:
        return "1x2", x
    x = pin_fair.get(("ml", None))
    if x:
        return "ml", x
    return None, None


def _fit_poisson(tag, pin_fair):
    kind, side = _side_target(pin_fair)
    tl, tp = _main(pin_fair, "total")
    if side is None or tl is None:
        return None
    ot = tag == "IH"

    def side_val(mg):
        h, d, a = p_side(mg)
        return (h - a) if kind == "1x2" else h

    target = (side["home"] - side["away"]) if kind == "1x2" else side["home"]
    s, T = 0.5, tl + 0.3
    for _ in range(4):
        def f_s(x):
            return side_val(poisson_dists(T * x, T * (1 - x), ot)[0])
        s = _bisect(f_s, 0.03, 0.97, target)

        def f_t(x):
            return p_total(poisson_dists(x * s, x * (1 - s), ot)[1], tl, "over") or 0.5
        T = _bisect(f_t, 0.5, 14.0, tp["over"])
    mg, tt = poisson_dists(T * s, T * (1 - s), ot)
    return mg, tt, _err(pin_fair, mg, tt)


def _fit_normal(tag, pin_fair):
    kind, side = _side_target(pin_fair)
    tl, tp = _main(pin_fair, "total")
    if side is None or tl is None:
        return None
    sm = _f(tag + "_SD_MARGIN", SD[tag][0])
    st = _f(tag + "_SD_TOTAL", SD[tag][1])
    ph = side["home"] / (side["home"] + side["away"])
    mu = sm * N.inv_cdf(min(max(ph, 0.01), 0.99))
    nu = tl + st * N.inv_cdf(min(max(tp["over"], 0.01), 0.99))
    mg, tt = normal_dists(mu, sm, nu, st)
    return mg, tt, _err(pin_fair, mg, tt)


def _err(pin_fair, mg, tt):
    worst = 0.0
    for (k, ln), d in pin_fair.items():
        if ln is None:
            continue
        if k == "ah":
            p = p_ah(mg, ln, "home")
            if p is not None and "home" in d:
                worst = max(worst, abs(p - d["home"]))
        elif k == "total":
            p = p_total(tt, ln, "over")
            if p is not None and "over" in d:
                worst = max(worst, abs(p - d["over"]))
    return worst


def ladder(tag: str, soft: dict, pin: dict):
    """-> {'ok': bool, 'err': float, 'rows': [...]} for every SportyBet handicap / total line. A row:
    {mkt, line, sel, odds, p, edge, exact} (exact = Pinnacle lists the same line)."""
    pf = fair_table(pin or {})
    res = fit(tag, pf) if pf else None
    if not res:
        return dict(ok=False, err=None, rows=[])
    mg, tt, err = res
    rows = []
    for mkt, fn, sels in (("ah", p_ah, ("home", "away")), ("total", p_total, ("over", "under"))):
        for ln, d in ((soft or {}).get(mkt) or {}).items():
            line = _num(ln)
            if line is None:
                continue
            exact = (mkt, line) in pf
            for sel in sels:
                if sel not in d:
                    continue
                p = pf[(mkt, line)][sel] if exact else (p_ah(mg, line, sel) if mkt == "ah" else p_total(tt, line, sel))
                if p is None:
                    continue
                rows.append(dict(mkt=mkt, line=line, sel=sel, odds=float(d[sel]), p=p, edge=float(d[sel]) * p - 1, exact=exact))
    return dict(ok=err is not None and err <= _f("FP_MAX_FIT_ERR", 0.04), err=err, rows=rows)
