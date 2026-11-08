"""How a FinalPoint card looks: header, one block per check (status, match story, price table, next check) and the MATCH ENDED block.
Everything here is plain text built from numbers FinalPoint already holds; nothing is guessed."""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

SEP = "━" * 15
SPORT = {"FB": "Football", "TN": "Tennis", "IH": "Ice Hockey", "AF": "American Football", "HB": "Handball", "VB": "Volleyball", "BB": "Basketball"}
TOTAL_WORD = {"FB": "GOALS", "IH": "GOALS", "HB": "GOALS", "AF": "POINTS", "BB": "POINTS", "VB": "POINTS", "TN": "GAMES"}
OT = {"AF": " (incl. overtime)", "BB": " (incl. overtime)", "IH": " (incl. overtime)", "FB": " (90 min)"}
MKT_NAME = {"1x2": "match result", "ml": "moneyline", "ah": "handicap", "total": "total", "es": "exact sets", "cs": "correct score", "sh": "set handicap"}
ORDER = {"1x2": 0, "ml": 0, "ah": 1, "total": 2}
E = html.escape


def wat(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc) + timedelta(hours=1)


def label_of(x):
    x = str(x or "Check")
    return x if x.lower().startswith(("check", "final", "last")) else "Check " + x


def disp(name):
    """'Liu, Claire' -> 'Claire Liu' (tennis feeds list surname first); other names unchanged."""
    name = str(name or "").strip()
    if "," in name and "/" not in name:
        a, b = [x.strip() for x in name.split(",", 1)]
        if a and b:
            return "%s %s" % (b, a)
    return name


def short(name, n=12):
    name = str(name or "")
    return name if len(name) <= n else name[:n].rstrip()


GENERIC = {"hc", "fc", "fk", "sc", "bc", "kk", "hk", "ac", "as", "cf", "cd", "ud", "sk", "afc", "bk", "if", "ik", "rc", "sv", "tsg", "ssc"}


def core(name):
    """'HC Yugra Khanty-Mansiysk' -> 'Yugra Khanty-Mansiysk': drop the club-type prefix so the distinctive word shows."""
    w = str(name or "").split()
    while len(w) > 1 and w[0].lower().strip(".") in GENERIC:
        w = w[1:]
    return " ".join(w)


def _pair(home, away, last=False):
    """One short word per team for the price table: the first word, or the last when both start the same ('Real Madrid' / 'Real Sociedad')."""
    h, a = core(home).split(), core(away).split()
    if h and a and (last or h[0].lower() == a[0].lower()):
        return h[-1], a[-1]
    return (h[0] if h else "Home"), (a[0] if a else "Away")


def row_label(mkt, line, sel, home, away, last=False):
    if mkt in ("1x2", "ml"):
        if last:
            home, away = _pair(home, away, True)
        return {"home": short(core(home)), "away": short(core(away)), "draw": "Draw"}.get(sel, str(sel))
    if mkt in ("ah", "sh"):
        if line is None:
            return str(sel)
        ln = "%+g" % (line if sel == "home" else -line)
        nm = _pair(home, away, last)[0 if sel == "home" else 1]
        return "%s %s" % (nm[:max(4, 12 - len(ln) - 1)], ln)
    if mkt == "total":
        return "%s %g" % (str(sel).title(), line)
    if mkt == "es":
        return "2 sets" if sel == "s2" else "3 sets"
    return "%s %s" % (sel, "" if line is None else "%g" % line)


def group_title(tag, mkt, closest):
    if mkt in ("ml", "1x2"):
        return ("MONEYLINE" if mkt == "ml" else "MATCH RESULT") + OT.get(tag, "")
    if mkt == "ah":
        return ("SPREAD" if tag in ("AF", "BB") else "HANDICAP") + OT.get(tag, "") + (" (closest line)" if closest else "")
    if mkt == "total":
        return "TOTAL %s%s" % (TOTAL_WORD.get(tag, "POINTS"), " (closest line)" if closest else "")
    return MKT_NAME.get(mkt, str(mkt)).upper()


MARK = {"VALIDATED": "V", "POSSIBLE": "P", "WITHDRAWN": "W", "WATCH": "C"}
LEGEND = "★ top 3 · V validated · P possible · C closest to value · D derived · M model-priced"


def render_table(groups, derived=False):
    """groups: [{title, rows:[{l, o, f, e, s, t, m}]}] -> html block. Monospace, one short line per market, so nothing wraps on a phone."""
    if not groups:
        return ""
    w = max(len(r["l"]) for g in groups for r in g["rows"])
    lines, last = [], None
    for g in groups:
        if g["title"] != last:
            lines.append(g["title"])
        last = g["title"]
        for r in g["rows"]:
            tags = ""
            w0 = str(r.get("t") or "").split(" ")[0]
            k = ("C" if r.get("s") else "") if w0 in ("WATCH", "") else MARK.get(w0, "")
            if k:
                tags += "[%s]" % k
            if r.get("m"):
                tags += "[%s]" % ("D" if r["m"] == "derived" else "M")
            lines.append("%s%s %s|%s|%s%s" % ("★" if r.get("s") else " ", r["l"].ljust(w), "%.2f" % r["o"], "%.2f" % r["f"],
                                              "%+.1f%%" % (100 * r["e"]), (" " + tags) if tags else ""))
    head = "SportyBet | %s | edge" % ("model fair" if derived else "Pinnacle fair")
    return "<i>%s</i>\n<pre>%s</pre>" % (head, E("\n".join(lines)))


# ---------------------------------------------------------------- match story
def _fmt_p(x):
    return "%.0f%%" % (100 * x)


def _ml_probs(c):
    p = c.get("p") or {}
    for pre in ("1x2||", "ml||"):
        got = {s: p[pre + s] for s in ("home", "draw", "away") if pre + s in p}
        if "home" in got and "away" in got:
            return got
    return None


def _plain_flag(f):
    if f.startswith("C1"):
        return "Pinnacle's moneyline and spread disagree by a wide margin, so the underdog spread may be underpriced."
    if f.startswith("C2"):
        return "The total line dropped but the Under did not get more likely: a possible trap on the Under."
    if f.startswith("C3"):
        return "The spread moved but the moneyline barely did: check that the spread move is real."
    return ""


def story(rec, c, prev, fy, top):
    """3-5 plain-English bullets: what Pinnacle says, what SportyBet says, what FinalPoint thinks, where to look."""
    home, away = rec.get("home") or "Home", rec.get("away") or "Away"
    name = {"home": home, "away": away, "draw": "Draw"}
    lvl, streak = fy[0], fy[2]
    L = []
    ml = _ml_probs(c)
    sp = c.get("ml") or {}
    sp_ok = "home" in sp and "away" in sp
    sp_m = (sum(1 / float(v) for v in sp.values()) - 1) if sp_ok else None

    # ---- Pinnacle
    if not c["pin"]:
        seen = any(x["pin"] for x in rec["checks"][:-1])
        if seen:
            L.append("Pinnacle is not pricing this match now (it did earlier). A price that was there and went missing is a warning: "
                     "news such as an injury or lineup, or a market pulled.")
        elif lvl == 0 and "usual" in str(fy[1]):
            L.append("Pinnacle is not pricing this match. That is usual for this league, so it is not a warning on its own.")
        elif lvl == 0:
            L.append("Pinnacle is not pricing this match. FinalPoint has not seen enough of this league yet to say if that is normal.")
        else:
            L.append("Pinnacle has not priced this match for %d checks in a row. That is the warning sign: it often means news, or a market pulled." % streak)
    elif ml:
        if "draw" in ml:
            L.append("Pinnacle: %s %s, Draw %s, %s %s." % (home, _fmt_p(ml["home"]), _fmt_p(ml["draw"]), away, _fmt_p(ml["away"])))
        else:
            fav = "home" if ml["home"] >= ml["away"] else "away"
            dog = "away" if fav == "home" else "home"
            if abs(ml["home"] - ml["away"]) < 0.06:
                L.append("Pinnacle sees a near coin-flip: %s %s, %s %s." % (home, _fmt_p(ml["home"]), away, _fmt_p(ml["away"])))
            else:
                L.append("Pinnacle makes %s the favourite at %s (fair odds %.2f), %s %s." % (name[fav], _fmt_p(ml[fav]), 1 / ml[fav], name[dog], _fmt_p(ml[dog])))
    else:
        L.append("Pinnacle has priced %d lines for this match." % len(c.get("p") or {}))

    # ---- SportyBet
    if sp_ok:
        names = {"home": home, "away": away, "draw": "Draw"}
        L.append("SportyBet: %s, with about %.1f%% margin built into those prices." % (
            ", ".join("%s %.2f" % (names[k], float(v)) for k, v in sp.items() if k in names), 100 * sp_m))

    # ---- FinalPoint's view
    best = top[0] if top else None
    if best is None:
        L.append("FinalPoint: no market to rank at this check." if c["pin"] or c.get("derived") else
                 "FinalPoint: no fair price to compare against, so nothing is ranked.")
    elif best["call"].startswith("VALIDATED"):
        L.append("FinalPoint: %s pays %.2f against a fair %.2f (%+.1f%%). The gap %s, and the price is fresh on the exact line." % (
            best["d"], best["odds"], best["fair"], 100 * best["e"],
            "held across checks" if "held" in (best.get("note") or "") or best["call"].endswith("(holds)") else "is backed by Pinnacle moving toward it"))
    elif best["call"] == "POSSIBLE":
        L.append("FinalPoint: %s pays %.2f against a fair %.2f (%+.1f%%). Not confirmed: %s." % (
            best["d"], best["odds"], best["fair"], 100 * best["e"], (best.get("note") or "").replace("AUDIT: ", "") or "needs one more check"))
    else:
        L.append("FinalPoint: no market pays above the fair price yet. The closest is %s at %.2f against %.2f (%+.1f%%)." % (
            best["d"], best["odds"], best["fair"], 100 * best["e"]))
    if c.get("derived"):
        L.append("No Pinnacle price, so FinalPoint priced SportyBet's other lines from its own moneyline and main total. "
                 "That only shows where SportyBet's lines disagree with each other. It is never a validated pick.")

    # ---- movement since the last check
    ps, cs = (prev or {}).get("snap") or {}, c.get("snap") or {}
    mv = []
    try:
        if ps.get("ml") and cs.get("ml"):
            d = cs["ml"]["home"] - ps["ml"]["home"]
            if abs(d) >= 0.01:
                mv.append("Pinnacle moved toward %s by %.0f pts (%s to %s)" % (home if d > 0 else away, 100 * abs(d), _fmt_p(ps["ml"]["home" if d > 0 else "away"]),
                                                                              _fmt_p(cs["ml"]["home" if d > 0 else "away"])))
        if ps.get("total") and cs.get("total") and ps["total"][0] != cs["total"][0]:
            mv.append("its total moved %g to %g" % (ps["total"][0], cs["total"][0]))
        if ps.get("spread") and cs.get("spread") and ps["spread"][0] != cs["spread"][0]:
            mv.append("its spread moved %+g to %+g" % (ps["spread"][0], cs["spread"][0]))
    except Exception:
        pass
    if mv:
        L.append("Since the last check: " + "; ".join(mv) + ".")

    # ---- where to look / what may be hidden
    hid = []
    if best and not best.get("exact") and not c.get("derived"):
        hid.append("the gap is on a line Pinnacle does not list, so FinalPoint's model estimated the fair price: a lead, not proof")
    if c.get("age") is not None and c["age"] > 10 and c["pin"]:
        hid.append("Pinnacle's price is %.0f min old, so any gap may be stale" % c["age"])
    only = [MKT_NAME.get(k, k) for k in (c.get("only") or [])]
    if only and c["pin"]:
        hid.append("SportyBet also lists %s, which Pinnacle does not price, so there is no fair price there" % ", ".join(only[:4]))
    for f in c.get("shadow") or []:
        if f.startswith("C1") and rec.get("tag") not in ("AF", "BB"):      # a 69% favourite at -1.5 is normally far below 69%: noise outside spread sports
            continue
        t = _plain_flag(f)
        if t:
            hid.append((t if t.split(" ")[0] in ("Pinnacle", "Pinnacle's", "SportyBet", "SportyBet's", "FinalPoint") else t[0].lower() + t[1:]).rstrip("."))
    if hid:
        L.append("Where to look: " + "; ".join(hid) + ".")
    return L[:6]


# ---------------------------------------------------------------- whole card
def header(rec):
    tag = rec["tag"]
    return ["<b>FINALPOINT: %s</b>" % E(SPORT.get(tag, tag)), E(str(rec.get("league") or "")),
            "<b>%s vs</b>" % E(str(rec.get("home") or "")), "<b>%s</b>" % E(str(rec.get("away") or "")),
            (wat(rec["tip"]).strftime("%a %d %b %H:%M") + " WAT") if rec.get("tip") else ""]


def block(c):
    if not c.get("story") and not c.get("tbl") and not c.get("st"):          # a check saved by the older card layout: keep it as one line
        return [SEP, "<b>%s</b> %s" % (E(label_of(c.get("label"))), wat(c["t"]).strftime("%H:%M") if c.get("t") else ""),
                "Seen before the new layout: no price table was kept for this check."]
    L = [SEP, "<b>%s</b> %s" % (E(label_of(c.get("label"))), wat(c["t"]).strftime("%H:%M") if c.get("t") else ""),
         "<b>Status:</b> %s" % E(c.get("st") or ""), "<b>Match Story:</b>"]
    L += ["• " + E(s) for s in c.get("story") or []]
    if c.get("tbl"):
        L.append(render_table(c["tbl"], bool(c.get("derived"))))
    if c.get("nxt"):
        L.append("")
        L.append("Next check: %s" % wat(c["nxt"]).strftime("%H:%M"))
    return L


def ended(end):
    L = [SEP, "<b>MATCH ENDED</b> · %s" % E(end.get("score") or "")]
    for g in end.get("picks") or []:
        L.append("%s <b>%s: %s.</b> %s" % ("★", E(g["d"]), E(g["res"]), E(g["why"])))
    if end.get("note"):
        L.append(E(end["note"]))
    if end.get("lesson"):
        L.append("<b>Lesson:</b> " + E(end["lesson"]))
    return L


def render(rec):
    L = [x for x in header(rec) if x] + ["<i>%s</i>" % LEGEND]
    for c in rec["checks"]:
        L += block(c)
    if rec.get("end"):
        L += ended(rec["end"])
    return "\n".join(L)
