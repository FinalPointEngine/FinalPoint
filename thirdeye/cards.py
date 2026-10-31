"""Telegram text for ThirdEye commands (HTML)."""
from __future__ import annotations

import html

from core import cards as cc
from thirdeye import cfg
from thirdeye.engine import ICON, local, STAKE


def _line(v):
    return "%s %s #%s %s v %s | %s @ %.2f | %d" % (ICON[v["label"]], v["sport"][:4].upper(), v["id"], v["home"], v["away"], v["desc"], v["odds"], v["score"])


def board_card(title, b):
    if not b["n"]:
        return cc.card("\U0001F441", title, "Engine alone against engine + ThirdEye, on graded picks.", note="No graded picks yet.")
    rows = [("Graded picks", "%d (won %d)" % (b["n"], b["won"])),
            ("Engine alone", "%+.2f units (%+.1f%% per pick)" % (b["eng_units"], b["eng_roi"] * 100)),
            ("Engine + ThirdEye", "%+.2f units (%+.1f%% per stake)" % (b["te_units"], b["te_roi"] * 100)),
            ("Approved", "%d (won %d)" % (b["approved"], b["approved_won"])),
            ("Held back", "%d (would have made %+.2f units)" % (b["rejected"], b["rejected_ret"])),
            ("CLV proxy", ("%+.1f%% over %d" % (b["clv"] * 100, b["clv_n"])) if b["clv"] is not None else "n/a")]
    return cc.card("\U0001F441", title, "Engine alone against engine + ThirdEye, on graded picks (1 unit = one full stake).", rows=rows,
                   note="Under about 30 graded picks this says very little. If held-back picks keep making money, the rules are too strict.")


def today_card(vs):
    return cc.card("\U0001F441", "THIRDEYE TODAY (%d)" % len(vs), sections=[("", [_line(v) for v in vs] or ["No verdicts yet."])])


def open_card(vs):
    return cc.card("\U0001F441", "OPEN VERDICTS (%d)" % len(vs), sections=[("", ["%s | %s" % (_line(v), local(v["tip"]).strftime("%a %H:%M")) for v in vs[:40]] or ["None."])])


def sports_card(eye):
    lines = []
    for k, lab, _, _ in cfg.SPORTS:
        s = eye.status.get(k)
        if k not in eye.readers or not eye.readers[k].connected():
            lines.append("%s: not connected" % lab)
        elif not s:
            lines.append("%s: waiting for first read" % lab)
        elif s["error"]:
            lines.append("%s: ERROR %s" % (lab, s["error"][:80]))
        else:
            lines.append("%s: %d picks read" % (lab, s["picks"]))
    return cc.card("\U0001F50C", "ENGINE CONNECTIONS", "Which engine databases ThirdEye can read.", sections=[("", lines)])


def rules_card():
    lines = ["Start at 50 points, then add or subtract:",
             "Edge size: 6-10% +10, 10-15% +5, 15%+ suspicious -10, 25%+ -25",
             "Persistence: held 3+ checks +15, 2 checks +8, 1 check -3",
             "Fair-probability drift: up +10, down 2+ pts -20",
             "Stale Pinnacle price -15; long odds / low win chance -10",
             "Kick-off passed -30; under 10 min -8",
             "Sport record over 20+ graded: -10 or +5",
             "Correlated picks / crowded sport -10 / -5",
             "Veto (always REJECT): edge over 30%, win chance under 25%, odds under 1.20",
             "APPROVE %d+, REDUCE %d+, WATCH %d+, else REJECT" % (cfg.approve(), cfg.reduce_(), cfg.watch())]
    return cc.card("\U0001F4D0", "THIRDEYE RULES", "Its own rules, applied the same way to every sport.", sections=[("", lines)])
