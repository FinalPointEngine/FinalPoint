"""Combine the rules into one graded verdict."""
from __future__ import annotations

from thirdeye import cfg, rules

LABELS = {"APPROVE": 1.0, "REDUCE": 0.5, "WATCH": 0.0, "REJECT": 0.0}


def judge(p, now, rec=None, open_same_match=0, open_sport=0):
    score, reasons = 50.0, []
    for pts, why in (rules.r_edge(p), rules.r_persistence(p), rules.r_drift(p), rules.r_stale(p), rules.r_range(p),
                     rules.r_timing(p, now), rules.r_record(rec), rules.r_exposure(p, open_same_match, open_sport)):
        score += pts
        if why:
            reasons.append((pts, why))
    for pts, why in p.get("extra") or []:          # stats checks (form, rest, H2H, own ratings)
        score += pts
        reasons.append((pts, why))
    score = max(0.0, min(100.0, score))
    if p["ev"] < 0:                       # no value: never better than DISAGREED, whatever else looks good
        score = min(score, cfg.watch() - 1)
    elif p["ev"] < 0.03:                  # under 3%: not enough value, never better than INDIFFERENT
        score = min(score, cfg.reduce_() - 1)
    v = rules.veto(p)
    if v:
        label, reasons = "REJECT", [(-99, v)] + reasons
    elif score >= cfg.approve():
        label = "APPROVE"
    elif score >= cfg.reduce_():
        label = "REDUCE"
    elif score >= cfg.watch():
        label = "WATCH"
    else:
        label = "REJECT"
    return dict(label=label, score=round(score), mult=LABELS[label], reasons=[w for _, w in sorted(reasons, key=lambda r: -abs(r[0]))])
