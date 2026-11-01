"""Results ledger: find the final score of a match in ThirdEye's own results history and settle any selection against it.
Uses each sport's own settle() so ThirdEye grades exactly the way the engines do (quarter lines, pushes, overtime rules)."""
from __future__ import annotations

import importlib

from football import names

MODELS = {"football": "football.model", "tennis": "tennis.model", "hockey": "hockey.model", "afootball": "afootball.model",
          "handball": "handball.model", "volleyball": "volleyball.model"}
WINDOW = 8 * 3600


def find_result(wh, sport, home, away, tip, cache=None):
    """(home score, away score) of the finished game, or None. Same two teams, started within 8 hours of the expected time."""
    if not wh or not tip:
        return None
    k = (sport, home, away, int(tip))
    if cache is not None and k in cache:
        return cache[k]
    out = None
    for t, h, a, hg, ag in wh.games(sport):
        if abs(t - tip) <= WINDOW and names.same_team(h, home) and names.same_team(a, away):
            out = (hg, ag)
            break
    if cache is not None and out is not None:
        cache[k] = out
    return out


def settle(sport, key, odds, hg, ag):
    """(status, ret) of one selection key ('mkt|line|sel') against a final score, or None when it cannot be graded safely."""
    mod = MODELS.get(sport)
    if not mod or not key or key.count("|") < 2:
        return None
    try:
        mkt, line, sel = (key.split("|") + ["", ""])[:3]
        c = dict(mkt=mkt, line=float(line) if line != "" else None, sel=sel, odds=float(odds))
        m = importlib.import_module(mod)
        if sport == "tennis":
            if mkt != "ml":
                return None                          # only a win/loss is known here, never games or sets
            return m.settle(c, hg, ag, home_won=hg > ag)
        return m.settle(c, hg, ag)
    except Exception:
        return None
