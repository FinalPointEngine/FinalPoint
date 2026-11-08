"""Two daily boards for the FinalPoint group, each ONE pinned message (more only if it outgrows Telegram's limit) edited in place:
  TODAY'S GAMES       every game FinalPoint is analysing today, one line each, a section per sport, newest game under the older ones.
  VALIDATED & LEADS   only the picks worth acting on, one line each, no stories, grouped by sport, with a link to the full card.
FinalPoint's own cards are not touched by any of this. Everything here is built from what FinalPoint already holds."""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

E = html.escape
ORDER = ["FB", "BB", "AF", "IH", "TN", "HB", "VB"]
NAME = {"FB": "FOOTBALL", "BB": "BASKETBALL", "AF": "AMERICAN FOOTBALL", "IH": "ICE HOCKEY", "TN": "TENNIS", "HB": "HANDBALL", "VB": "VOLLEYBALL"}
LIMIT = 3800


def wat(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc) + timedelta(hours=1)


def day_of(tip, now=None):
    return wat(tip or now or 0).strftime("%Y-%m-%d")


def link_base(chat):
    """https://t.me/c/<id>/ for a supergroup id such as -1004303651954, else '' (no links)."""
    s = str(chat or "")
    return "https://t.me/c/%s/" % s[4:] if s.startswith("-100") and s[4:].isdigit() else ""


def _status(rec, c):
    if rec.get("end"):
        return "FT"
    if c.get("wd_now"):
        return "WITHDRAWN"
    picks = c.get("picks") or []
    if any(p.get("tier") == 0 for p in picks):
        return "VALIDATED"
    if any(p.get("tier") == 1 for p in picks):
        return "LEAD"
    if c.get("verdict") == "NO PRICE":
        return "NO PRICE"
    return "WATCH"


def entry(rk, rec, seen):
    """The small record the boards keep per match."""
    c = (rec.get("checks") or [{}])[-1]
    keep = ("d", "odds", "e", "pkey")
    e = dict(rk=rk, tag=rec.get("tag"), home=rec.get("home"), away=rec.get("away"), tip=rec.get("tip"), seen=seen, st=_status(rec, c), ck=c.get("t"),
             card=(rec.get("fp") or [None])[0],
             early=bool(rec.get("tip") and c.get("t") and float(rec["tip"]) - float(c["t"]) > 3900 and not rec.get("end")),
             val=[{k: p.get(k) for k in keep} for p in (c.get("picks") or []) if p.get("tier") == 0],
             lead=[{k: p.get(k) for k in keep} for p in (c.get("picks") or []) if p.get("tier") == 1])
    end = rec.get("end")
    if end:
        e["end"] = end.get("score")
        e["res"] = {g.get("pkey"): g.get("status") for g in end.get("picks") or [] if g.get("pkey")}
        e["val"] = [p for p in e["val"] if p["pkey"] in e["res"]] or e["val"]
        e["lead"] = [p for p in e["lead"] if p["pkey"] in e["res"]] or e["lead"]
    return e


def _cut(name, n=13):
    from finalpoint.card import core
    name = core(name)
    return name if len(name) <= n else name[:n].rstrip()


def _title(e):
    return "%s v %s" % (_cut(e.get("home")), _cut(e.get("away")))


def _a(base, mid, text):
    return '<a href="%s%s">%s</a>' % (base, mid, E(text)) if base and mid else E(text)


def _pages(head, lines):
    pages, cur = [], head
    for ln in lines:
        if len(cur) + len(ln) + 1 > LIMIT and cur != head:
            pages.append(cur)
            cur = head + " · page %d" % (len(pages) + 1)
        cur += "\n" + ln
    pages.append(cur)
    return pages


def _by_sport(ents):
    out = {}
    for e in ents:
        out.setdefault(e.get("tag"), []).append(e)
    return [(t, out[t]) for t in ORDER + sorted(k for k in out if k not in ORDER) if t in out]


def render_games(day, ents, base="", other=None, now=None):
    """TODAY'S GAMES: a section per sport, games in the order they appeared (newest last)."""
    now = now or datetime.now(timezone.utc).timestamp()
    head = "<b>FINALPOINT · TODAY'S GAMES</b> · %s" % datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
    secs = _by_sport([e for e in ents])
    lines = ["<i>updated %s · %d games%s</i>" % (wat(now).strftime("%H:%M"), len(ents),
                                                   (" · " + _a(base, other, "Validated & Leads")) if other and base else "")]
    if secs:
        lines.append("Jump: " + " · ".join(_a(base, sorted(g, key=lambda e: e.get("seen") or 0)[0].get("card"), "%s %d" % (t, len(g))) for t, g in secs))
    for t, g in secs:
        g = sorted(g, key=lambda e: e.get("seen") or 0)
        lines += ["", "<b>%s</b> · %d" % (NAME.get(t, t), len(g))]
        for e in g:
            st = ("FT " + str(e["end"])) if e.get("end") else e.get("st", "WATCH")
            lines.append("%s %s · %s" % (wat(e["tip"]).strftime("%H:%M") if e.get("tip") else "--:--", _a(base, e.get("card"), _title(e)), E(str(st))))
    if not secs:
        lines.append("No game analysed yet today.")
    return _pages(head, lines)


MARK = {"won": " ✅", "halfwon": " ✅", "lost": " ❌", "halflost": " ❌", "void": " ➖"}


def render_picks(day, ents, base="", other=None, record=None, now=None):
    """VALIDATED & LEADS: only matches with a VALIDATED pick or a lead. One line per pick, no story."""
    now = now or datetime.now(timezone.utc).timestamp()
    head = "<b>FINALPOINT · VALIDATED &amp; LEADS</b> · %s" % datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
    r = record or {}
    lines = ["<i>updated %s · VALIDATED record so far: %dW-%dL%s</i>" % (wat(now).strftime("%H:%M"), r.get("w", 0), r.get("l", 0),
                                                                         (" · " + _a(base, other, "Today's Games")) if other and base else "")]

    def section(title, key, note):
        rows = [e for e in ents if e.get(key)]
        lines.append("")
        lines.append("<b>%s</b>%s" % (title, (" <i>%s</i>" % note) if note else ""))
        if not rows:
            lines.append("None right now." if key == "lead" else "None yet today.")
            return
        for t, g in _by_sport(rows):
            lines.append("<b>%s</b>" % NAME.get(t, t))
            for e in sorted(g, key=lambda e: e.get("tip") or 0):
                for p in e[key]:
                    res = MARK.get((e.get("res") or {}).get(p.get("pkey")), "")
                    lines.append("%s %s · %s · %.2f · %+.1f%%%s · as of %s%s" % (
                        wat(e["tip"]).strftime("%H:%M") if e.get("tip") else "--:--", _a(base, e.get("card"), _title(e)), E(str(p.get("d"))),
                        float(p.get("odds") or 0), 100 * float(p.get("e") or 0), res, wat(e["ck"]).strftime("%H:%M") if e.get("ck") else "--:--",
                        " · early" if e.get("early") else ""))

    section("VALIDATED", "val", "")
    section("LEADS", "lead", "(not bets yet)")
    lines += ["", "<i>early = last checked more than 1 hour before kickoff: wait for the T-60 / T-30 check. Open the card and read its latest block before you play. A pick that shows WITHDRAWN is off.</i>"]
    return _pages(head, lines)
