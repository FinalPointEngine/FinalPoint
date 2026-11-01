"""ThirdEye's independent stats model, built only from its own results history: power ratings, form, rest days, head-to-head,
expected margin and total. Pure maths over Warehouse.games(); returns None whenever there is too little data."""
from __future__ import annotations

import math
import statistics

from football import names
from thirdeye import cfg

POINTS_SPORTS = ("basketball", "afootball")


def cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class Stats:
    def __init__(self, warehouse):
        self.wh = warehouse
        self._built = {}          # sport -> (version, model)
        self._match = {}

    # ---------------------------------------------------------------- ratings
    def _model(self, sport):
        hit = self._built.get(sport)
        if hit and hit[0] == self.wh.version:
            return hit[1]
        games = self.wh.games(sport)
        by_team, margins, totals = {}, [], []
        for t, h, a, hg, ag in games:
            hk, ak = names.norm(h), names.norm(a)
            by_team.setdefault(hk, dict(name=h, g=[]))["g"].append((t, ak, hg, ag, True))
            by_team.setdefault(ak, dict(name=a, g=[]))["g"].append((t, hk, ag, hg, False))
            margins.append(hg - ag)
            totals.append(hg + ag)
        model = None
        if len(games) >= 30:
            hadv = sum(margins) / len(margins)
            rating = {k: 0.0 for k in by_team}
            for _ in range(15):
                new = {}
                for k, tm in by_team.items():
                    s = sum((pf - pa) - (hadv / 2 if home else -hadv / 2) + rating.get(opp, 0.0) for _, opp, pf, pa, home in tm["g"])
                    new[k] = s / (len(tm["g"]) + 3.0)                 # shrink: few games = rating near average
                rating = new
            model = dict(by=by_team, r=rating, hadv=hadv, sd_m=statistics.pstdev(margins) or 1.0, sd_t=statistics.pstdev(totals) or 1.0,
                         avg_total=sum(totals) / len(totals))
        self._built[sport] = (self.wh.version, model)
        self._match = {k: v for k, v in self._match.items() if k[0] != sport}
        return model

    def _team(self, sport, model, name):
        key = (sport, name)
        if key in self._match:
            return self._match[key]
        k = names.norm(name)
        found = k if k in model["by"] else None
        if not found:
            best = 0.0
            for cand in model["by"]:
                if cand[:1] != k[:1] and cand[-1:] != k[-1:]:
                    continue
                s = names.similarity(name, model["by"][cand]["name"])
                if s > best and names.same_team(name, model["by"][cand]["name"]):
                    best, found = s, cand
        self._match[key] = found
        return found

    # ---------------------------------------------------------------- one match
    def view(self, sport, home, away, tip):
        """dict(n_h, n_a, exp_margin, exp_total, sd_m, sd_t, form_h, form_a, rest_h, rest_a, h2h=(w_h, d, w_a)) or None."""
        try:
            model = self._model(sport)
            if not model:
                return None
            hk, ak = self._team(sport, model, home), self._team(sport, model, away)
            if not hk or not ak or hk == ak:
                return None
            th, ta = model["by"][hk]["g"], model["by"][ak]["g"]
            gh = [g for g in th if g[0] < tip]
            ga = [g for g in ta if g[0] < tip]
            need = int(cfg.f("TE_MIN_G", 6))
            if len(gh) < need or len(ga) < need:
                return None

            def avg(g, i):
                return sum(x[i] for x in g) / len(g)

            exp_margin = model["r"][hk] - model["r"][ak] + model["hadv"]
            exp_total = ((avg(gh, 2) + avg(ga, 3)) + (avg(ga, 2) + avg(gh, 3))) / 2.0

            def form(g):
                return "".join("W" if x[2] > x[3] else ("D" if x[2] == x[3] else "L") for x in g[-5:])

            h2h = [g for g in gh if g[1] == ak][-5:]
            return dict(n_h=len(gh), n_a=len(ga), exp_margin=exp_margin, exp_total=exp_total, sd_m=model["sd_m"], sd_t=model["sd_t"], form_h=form(gh),
                        form_a=form(ga), rest_h=(tip - gh[-1][0]) / 86400.0, rest_a=(tip - ga[-1][0]) / 86400.0,
                        h2h=(sum(1 for g in h2h if g[2] > g[3]), sum(1 for g in h2h if g[2] == g[3]), sum(1 for g in h2h if g[2] < g[3])))
        except Exception:
            return None


def p_for_key(view, key, sport):
    """Stats probability of one selection, or None when the market is not modelled."""
    if not view:
        return None
    mkt, line, sel = (key.split("|") + ["", ""])[:3]
    m, sd = view["exp_margin"], view["sd_m"]
    try:
        ln = float(line) if line != "" else None
    except ValueError:
        return None
    if mkt in ("ml", "h2h"):
        ph = cdf(m / sd)
        return ph if sel == "home" else (1 - ph if sel == "away" else None)
    if mkt == "1x2":
        ph, pa = cdf((m - 0.5) / sd), cdf((-m - 0.5) / sd)
        return {"home": ph, "away": pa, "draw": max(0.0, 1 - ph - pa)}.get(sel)
    if sport == "tennis":
        return None
    if mkt == "total" and ln is not None:
        over = 1 - cdf((ln - view["exp_total"]) / view["sd_t"])
        return over if sel == "over" else (1 - over if sel == "under" else None)
    if mkt in ("ah", "spreads", "hc") and ln is not None:
        ph = cdf((m + ln) / sd)
        return ph if sel == "home" else (1 - ph if sel == "away" else None)
    return None


def extras(view, key, p_stats, p_price, hist):
    """Score adjustments from the stats checks: [(points, reason)]."""
    out = []
    if p_stats is None:
        return out
    d = p_stats - p_price
    if d >= 0.04:
        out.append((8, "Own ratings back it: %.0f%% win chance vs %.0f%% from the price" % (p_stats * 100, p_price * 100)))
    elif d <= -0.06:
        out.append((-15, "Own ratings disagree: %.0f%% vs %.0f%% from the price" % (p_stats * 100, p_price * 100)))
    if len(hist) >= 2 and hist[-1][1] - hist[0][1] >= 0.01 and d <= -0.04:
        out.append((-10, "Possible false noise: Pinnacle moved toward it but ratings do not follow"))
    sel = key.split("|")[2] if key.count("|") >= 2 else ""
    if sel in ("home", "away"):
        mine, opp = ("h", "a") if sel == "home" else ("a", "h")
        if view["rest_" + mine] < 1.3:
            out.append((-5, "Back-to-back: only %.1f days of rest" % view["rest_" + mine]))
        elif view["rest_" + opp] < 1.3:
            out.append((3, "Opponent is on a back-to-back"))
        f = view["form_" + mine]
        if f.count("W") >= 4:
            out.append((3, "Good form (last 5: %s)" % f))
        elif f.count("W") <= 1 and len(f) == 5:
            out.append((-4, "Poor form (last 5: %s)" % f))
        w_h, dr, w_a = view["h2h"]
        if w_h + dr + w_a >= 3:
            w = w_h if sel == "home" else w_a
            if w >= 4:
                out.append((3, "Won %d of the last %d meetings" % (w, w_h + dr + w_a)))
            elif w <= 1:
                out.append((-3, "Won only %d of the last %d meetings" % (w, w_h + dr + w_a)))
    return out


def line(view):
    """One short line for the card."""
    if not view:
        return None
    w_h, dr, w_a = view["h2h"]
    return "\U0001F4CA Form %s v %s · Rest %.0fd v %.0fd · H2H %d-%d-%d" % (view["form_h"], view["form_a"], view["rest_h"], view["rest_a"], w_h, dr, w_a)
