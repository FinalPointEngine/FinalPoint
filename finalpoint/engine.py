"""FinalPoint: takes every check an engine sends (core/fpfeed.py), keeps the match history, prices the whole market ladder and
posts ONE card per match to the group. The card grows: every check adds a block listing the markets seen at that check with a
call (POSSIBLE / WATCH / VALIDATED / AUDIT / FADING / WITHDRAWN); the best 3 are starred at every check; a market that holds on
two checks is VALIDATED so the reader need not wait for the later checks (which keep running and can WITHDRAW it)."""
from __future__ import annotations

import html
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from finalpoint import ladder as ld, rules
from finalpoint.rules import describe, edge

log = logging.getLogger("finalpoint")
ICON = {"FB": "⚽", "TN": "🎾", "IH": "🏒", "AF": "🏈", "HB": "🤾", "VB": "🏐", "BB": "🏀"}
SEP = "━" * 15
LATE = ("final", "t15", "last")
MAX_LIST = 5
BB_MKT = {"h2h": "ml", "spreads": "ah", "totals": "total"}


def _local(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc) + timedelta(hours=1)        # WAT


def _pk(mkt, line, sel):
    return "%s|%s|%s" % (mkt, "" if line is None else "%g" % line, sel)


def _label(x):
    x = str(x or "Check")
    return x if x.lower().startswith(("check", "final", "last")) else "Check " + x


class FinalPoint:
    def __init__(self, load, save, mirror):
        """load(key) -> str|None, save(key, str), mirror(rec, text) posts / edits the card of one match."""
        self._load, self._save, self._post = load, save, mirror
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- store
    def _rd(self, k, d):
        try:
            raw = self._load(k)
            return json.loads(raw) if raw else d
        except Exception:
            return d

    def _wr(self, k, v):
        try:
            self._save(k, json.dumps(v))
        except Exception as e:
            log.warning("state not saved (%s): %s", k, e)

    # ---------------------------------------------------------------- feed
    def feed(self, p: dict) -> dict:
        if not isinstance(p, dict) or not p.get("tag") or not p.get("key") or not p.get("chk"):
            return {"ok": False, "why": "bad payload"}
        with self._lock:
            return self._feed(p)

    def _feed(self, p):
        tag, key = p["tag"], str(p["key"])
        rk = "fp:%s:%s" % (tag, key)
        rec = self._rd(rk, None) or dict(tag=tag, key=key, checks=[], fp=[], fp_h=[], val={}, wd={})
        rec.update(home=p.get("home"), away=p.get("away"), league=p.get("league"), tip=p.get("tip"), min_ev=p.get("min_ev", 0.04))
        rec.setdefault("val", {})
        rec.setdefault("wd", {})
        c = self._analyse(rec, p, p["chk"])
        rec["checks"].append(c)
        rec["checks"] = rec["checks"][-14:]
        rec["top3"] = c.get("top3") or rec.get("top3") or []
        self._coverage(tag, rec["league"], c["pin"])
        text = self.report(rec)
        try:
            self._post(rec, text)
        except Exception as e:
            log.warning("card not posted: %s", e)
        self._wr(rk, rec)
        self._index(rk, rec.get("tip"))
        return {"ok": True}

    # ---------------------------------------------------------------- rows of one check
    @staticmethod
    def _rows_from_feed(tag, p, pin, soft):
        """-> (rows with Pinnacle's own price for the same line, pf). Each row: mkt, line, sel, odds, p, edge, exact."""
        rows, pf = [], {}
        if tag == "BB":
            for r in p.get("brows") or []:
                try:
                    mkt = BB_MKT.get(r["market"])
                    if mkt and r.get("odds") and r.get("p"):
                        rows.append(dict(mkt=mkt, line=r.get("line"), sel=r["selection"], odds=float(r["odds"]), p=float(r["p"]),
                                         edge=edge(float(r["odds"]), float(r["p"])), exact=True))
                except Exception:
                    continue
            return rows, pf
        pf = ld.fair_table(pin) if pin else {}
        for (mkt, line), d in pf.items():
            book = soft.get(mkt) or {}
            if line is None:
                odds_d = book if not any(isinstance(v, dict) for v in book.values()) else {}
            else:
                odds_d = book.get("%g" % line) or {}
            for sel, pr in d.items():
                if sel in odds_d:
                    rows.append(dict(mkt=mkt, line=line, sel=sel, odds=float(odds_d[sel]), p=pr, edge=edge(float(odds_d[sel]), pr),
                                     exact=True))
        return rows, pf

    # ---------------------------------------------------------------- analysis of one check
    def _analyse(self, rec, p, chk):
        tag, thr = rec["tag"], float(rec.get("min_ev") or 0.04)
        half = thr / 2
        soft, pin = p.get("soft") or {}, p.get("pin")
        has_pin = bool(pin) if tag != "BB" else bool(p.get("brows"))
        prev = rec["checks"][-1] if rec["checks"] else None
        age = chk.get("age")
        fresh = age is None or age <= rules.FRESH_MIN()
        c = dict(label=chk.get("label"), kind=chk.get("kind"), t=chk.get("t"), status=chk.get("status"), age=age,
                 pin=has_pin, nxt=chk.get("nxt"), held=chk.get("held"))
        rows, pf = self._rows_from_feed(tag, p, pin, soft)
        if has_pin and tag in ("FB", "IH", "AF", "HB"):
            lad = ld.ladder(tag, soft, pin)
            c["ladder_ok"], c["fit_err"], c["fit_where"] = lad["ok"], lad["err"], lad.get("where", "")
            if lad["ok"]:
                known = {(r["mkt"], r["line"], r["sel"]) for r in rows}
                rows += [r for r in lad["rows"] if (r["mkt"], r["line"], r["sel"]) not in known]
        for r in rows:
            r["pkey"] = _pk(r["mkt"], r["line"], r["sel"])
        pp = (prev or {}).get("p") or {}
        c["p"] = {r["pkey"]: round(r["p"], 4) for r in rows if r["exact"]}
        for r in rows:
            r["toward"] = r["pkey"] in pp and r["p"] - pp[r["pkey"]] >= 0.01
        hist = {}
        for ch in rec["checks"]:
            for k, v in (ch.get("e") or {}).items():
                hist.setdefault(k, []).append(v)
        listed_before = {k for ch in rec["checks"] for k in (ch.get("e") or {})}
        c["e"] = {r["pkey"]: round(r["edge"], 4) for r in rows if r["edge"] >= half}
        home, away = rec.get("home") or "", rec.get("away") or ""
        out = []
        for r in sorted(rows, key=lambda r: -r["edge"]):
            k, e = r["pkey"], r["edge"]
            was_val = k in rec["val"]
            call, note = None, ""
            if was_val:
                if e >= half:
                    call = "VALIDATED (holds)"
                else:
                    call, note = "WITHDRAWN", "edge gone: %+.1f%%" % (100 * e)
                    rec["val"].pop(k, None)
                    rec["wd"][k] = c["label"]
            elif e >= thr:
                why = []
                if not r["exact"]:
                    why.append("model-priced line, Pinnacle does not list it")
                elif e > rules.MAX_EDGE():
                    why.append("edge above %.0f%%, check it is the same market on both books" % (100 * rules.MAX_EDGE()))
                if not fresh:
                    why.append("Pinnacle price %.0f min old" % age)
                stable = bool(hist.get(k)) and hist[k][-1] >= thr
                if not why and (stable or r["toward"]):
                    call = "VALIDATED"
                    note = "held on 2 checks" if stable else "Pinnacle moved toward it"
                    rec["val"][k] = c["label"]
                else:
                    call = "POSSIBLE"
                    note = "AUDIT: " + (", ".join(why) if why else "first check with this edge, confirm at the next check")
            elif e >= half:
                call = "WATCH"
            elif k in listed_before:
                call, note = "FADING", "no value now"
            if call:
                out.append(dict(pkey=k, d=describe(r["mkt"], r["line"], r["sel"], home, away), odds=round(r["odds"], 2),
                                fair=round(1 / r["p"], 2), e=round(e, 4), call=call, note=note, exact=r["exact"]))
        rank = lambda o: (not o["call"].startswith("VALIDATED"), not o["exact"], -o["e"])      # validated first, Pinnacle-priced before model-priced
        top = sorted([o for o in out if o["call"] in ("VALIDATED", "VALIDATED (holds)", "POSSIBLE", "WATCH")], key=rank)[:3]
        for i, o in enumerate(top):
            o["star"] = i + 1
        c["top3"] = [o["pkey"] for o in top]
        # keep the listed rows: the starred first, then the rest, at most MAX_LIST
        keep = top + [o for o in out if o not in top]
        c["mk"] = keep[:MAX_LIST]
        c["closest"] = None
        if not top and rows:
            b = max(rows, key=lambda r: r["edge"])
            c["closest"] = dict(d=describe(b["mkt"], b["line"], b["sel"], home, away), odds=round(b["odds"], 2),
                                fair=round(1 / b["p"], 2), e=round(b["edge"], 4))
        c["verdict"] = ("VALIDATED" if any(o["call"].startswith("VALIDATED") for o in out) else
                        "POSSIBLE" if any(o["call"] == "POSSIBLE" for o in out) else "NO VALUE") if has_pin else "NO PRICE"
        if tag == "BB" and chk.get("status") == "play":
            c["engine_rows"] = [dict(desc="%s %s %s" % (r.get("market"), r.get("selection"), r.get("line") if r.get("line") is not None else ""),
                                     odds=r.get("odds"), ev=r.get("ev")) for r in (chk.get("rows") or [])][:3]
        ml = soft.get("1x2") or soft.get("ml") or {}
        c["ml"] = {k: v for k, v in ml.items() if not isinstance(v, dict)}
        c["only"] = sorted(k for k in soft if k not in (pin or {}))
        c["shadow"] = []
        try:
            cur = self._snap(pf)
            c["snap"] = cur
            c["shadow"] = rules.shadow((prev or {}).get("snap"), cur) if pin else []
        except Exception:
            pass
        return c

    @staticmethod
    def _snap(pf):
        ml = pf.get(("1x2", None)) or pf.get(("ml", None))
        out = {}
        if ml:
            h, a = ml["home"], ml["away"]
            out["ml"] = {"home": h / (h + a), "away": a / (h + a)}
        best = None
        for (k, ln), d in pf.items():
            if k == "ah" and ln is not None and (best is None or abs(d["home"] - 0.5) < best[0]):
                best = (abs(d["home"] - 0.5), ln, d["home"])
        if best:
            out["spread"] = (best[1], best[2])
        best = None
        for (k, ln), d in pf.items():
            if k == "total" and ln is not None and (best is None or abs(d["over"] - 0.5) < best[0]):
                best = (abs(d["over"] - 0.5), ln, d["under"])
        if best:
            out["total"] = (best[1], best[2])
        return out

    # ---------------------------------------------------------------- coverage (is Pinnacle normally there for this league?)
    def _coverage(self, tag, league, has_pin):
        cov = self._rd("fp_cov", {})
        for k in ("%s|%s" % (tag, league), "%s|*" % tag):
            n, w = cov.get(k, [0, 0])
            cov[k] = [n + 1, w + (1 if has_pin else 0)]
        self._wr("fp_cov", cov)
        self._cov = cov

    def _coverage_of(self, tag, league):
        cov = getattr(self, "_cov", None) or self._rd("fp_cov", {})
        n, w = cov.get("%s|%s" % (tag, league), [0, 0])
        if n < 10:
            n, w = cov.get("%s|*" % tag, [0, 0])
        return (w / n if n else 1.0), n

    def _index(self, rk, tip):
        idx = self._rd("fp_index", {})
        idx[rk] = tip or 0
        cut = time.time() - 4 * 86400
        for k in [k for k, v in idx.items() if v and v < cut]:
            idx.pop(k, None)
            try:
                self._save(k, "")
            except Exception:
                pass
        self._wr("fp_index", idx)

    # ---------------------------------------------------------------- fishy
    def _fishy(self, rec):
        ch, tag = rec["checks"], rec["tag"]
        streak = 0
        for x in reversed(ch):
            if x["pin"]:
                break
            streak += 1
        if streak == 0:
            return 0, "0 Normal", 0
        cov, n = self._coverage_of(tag, rec.get("league"))
        had_pin = any(x["pin"] for x in ch)
        usual = n >= 10 and cov >= 0.5                       # the price is normally there for this league / sport
        if not had_pin and not usual:
            why = "usual for this league (%d%% of checks)" % round(100 * cov) if n >= 10 else "baseline still building"
            return 0, "0 No Pinnacle price, %s" % why, streak
        lvl, lab = rules.fishy(streak, (not had_pin) and usual, ch[-1].get("kind") in LATE, cov, False)
        return lvl, lab, streak

    # ---------------------------------------------------------------- card
    def report(self, rec) -> str:
        e = html.escape
        tag, ch = rec["tag"], rec["checks"]
        c = ch[-1]
        home, away = rec.get("home") or "", rec.get("away") or ""
        lvl, lab, streak = self._fishy(rec)
        L = ["%s <b>FINALPOINT · %s · %s vs %s</b>" % (ICON.get(tag, ""), tag, e(home), e(away)),
             "%s · %s" % (e(str(rec.get("league") or "")), _local(rec["tip"]).strftime("%a %d %b %H:%M") if rec.get("tip") else "")]
        L += self._status(rec, c, lvl, lab)
        for x in ch:
            L += [SEP, "<b>%s</b> · %s" % (e(_label(x.get("label"))), _local(x["t"]).strftime("%H:%M") if x.get("t") else "")]
            if not x["pin"]:
                L.append("• No Pinnacle price: nothing to compare")
            elif x.get("mk"):
                for o in x["mk"]:
                    L.append("• %s%s · %.2f vs fair %.2f · edge %+.1f%% · <b>%s</b>%s%s" % (
                        ("★%d " % o["star"]) if o.get("star") else "", e(o["d"]), o["odds"], o["fair"], 100 * o["e"], o["call"],
                        (" (%s)" % e(o["note"])) if o["note"] else "", "" if o["exact"] else " [MODEL line]"))
            elif x.get("closest"):
                b = x["closest"]
                L.append("• No market with value. Closest: %s · %.2f vs fair %.2f · edge %+.1f%%" % (e(b["d"]), b["odds"], b["fair"], 100 * b["e"]))
            else:
                L.append("• No market with value at this check")
            for r in x.get("engine_rows") or []:
                L.append("• TheBeast: %s @ %s" % (e(str(r["desc"]).strip()), r.get("odds")))
        L.append(SEP)
        if c["pin"]:
            L.append("Pinnacle view: %s" % e(self._view(c, home, away)))
        else:
            L.append("Pinnacle MISSING: %d check%s in a row" % (streak, "s" if streak != 1 else ""))
        if lvl >= 2:
            L += ["<b>Hunt mode:</b> SportyBet-only markets: %s" % (e(", ".join(c.get("only") or [])) or "none listed"),
                  "Drift (SportyBet moneyline): %s" % e(self._drift(ch)),
                  "No fair price, so no edge can be shown. Watch only, do not bet blind."]
        if c.get("ladder_ok") is False and c["pin"] and c.get("fit_where"):
            L.append("Ladder off this check: model misses Pinnacle by %.1f points on %s" % (100 * (c.get("fit_err") or 0), e(c["fit_where"])))
        sh = c.get("shadow") or []
        if sh:
            L.append("Shadow flags (logged, not used): %s" % e("; ".join(sh)))
        L.append("TheBeast: %s" % ({"play": "PLAY NOW", "seen": "edge seen, waiting for a second check", "watch": "no pick",
                                    "nopin": "no Pinnacle price", "hold": "on hold"}.get(c.get("status"), str(c.get("status")))))
        L.append("History: %s" % e(" · ".join("%s %s" % (_label(x.get("label")).replace("Check ", "C").split(":")[0], x["verdict"]) for x in ch[-8:])))
        return "\n".join(L)

    def _status(self, rec, c, lvl, lab):
        e = html.escape
        L = []
        if lvl >= 2:
            L.append("<b>STATUS: FISHY - HUNT, DO NOT BET BLIND</b> (level %s)" % e(lab))
        elif lvl == 1:
            L.append("<b>STATUS: WATCH</b> (Pinnacle price missing 1 check)")
        elif not c["pin"]:
            L.append("<b>STATUS: NO PRICE</b> (reported only, never a bet)")
        else:
            last = {o["pkey"]: o for o in (c.get("mk") or [])}
            val = [last[k] for k in rec["val"] if k in last]
            aud = [o for o in (c.get("mk") or []) if o["call"] == "POSSIBLE"]
            if val:
                for o in val:
                    L.append("<b>STATUS: VALIDATED</b> · %s @ %.2f · since %s" % (e(o["d"]), o["odds"], e(_label(rec["val"][o["pkey"]]))))
            else:
                L.append("<b>STATUS: %s</b>" % ("AUDIT" if aud else "NO VALUE YET"))
            for o in aud[:3]:
                L.append("AUDIT: %s · %s" % (e(o["d"]), e(o["note"].replace("AUDIT: ", ""))))
            if rec.get("wd"):
                L.append("WITHDRAWN: %s" % e(", ".join("%s %s (%s)" % (k.split("|")[0], k.split("|")[1], v) for k, v in rec["wd"].items())))
            top = [o for o in (c.get("mk") or []) if o.get("star")]
            if top:
                L.append("BEST 3: %s" % e(" · ".join("★%d %s (%+.1f%%)" % (o["star"], o["d"], 100 * o["e"]) for o in top)))
            nxt = c.get("nxt")
            if nxt:
                L.append("Next check: %s" % _local(nxt).strftime("%H:%M"))
        return L

    @staticmethod
    def _view(c, home, away):
        p = c.get("p") or {}
        parts = []
        for k, lab in (("1x2||home", home), ("ml||home", home), ("1x2||away", away), ("ml||away", away), ("1x2||draw", "Draw")):
            if k in p:
                parts.append("%s %.0f%%" % (lab, 100 * p[k]))
        return "Moneyline: " + ", ".join(parts) if parts else "Pinnacle has priced %d lines" % len(p)

    @staticmethod
    def _drift(ch):
        out = []
        for x in ch:
            ml = x.get("ml") or {}
            if "home" in ml:
                out.append("%.2f" % ml["home"])
        return " -> ".join(out) if out else "no SportyBet moneyline price seen"
