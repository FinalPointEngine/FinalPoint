"""FinalPoint: takes every check an engine sends (core/fpfeed.py), keeps the match history, prices the whole market ladder and
posts ONE report per match to the group, edited after every check (long ones continue on a linked page)."""
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
SPORT = {"FB": "Football", "TN": "Tennis", "IH": "Ice hockey", "AF": "American football", "HB": "Handball", "VB": "Volleyball",
         "BB": "Basketball"}
ICON = {"FB": "⚽", "TN": "🎾", "IH": "🏒", "AF": "🏈", "HB": "🤾", "VB": "🏐", "BB": "🏀"}
SEP = "━" * 15
LATE = ("final", "t15", "last")


def _local(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc) + timedelta(hours=1)        # WAT


def _pk(mkt, line, sel):
    return "%s|%s|%s" % (mkt, "" if line is None else "%g" % line, sel)


class FinalPoint:
    def __init__(self, load, save, mirror):
        """load(key) -> str|None, save(key, str), mirror(rec, text) posts / edits the report of one match."""
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
        rec = self._rd(rk, None) or dict(tag=tag, key=key, home=p.get("home"), away=p.get("away"), league=p.get("league"),
                                         tip=p.get("tip"), checks=[], fp=[], fp_h=[], min_ev=p.get("min_ev", 0.04))
        rec.update(home=p.get("home"), away=p.get("away"), league=p.get("league"), tip=p.get("tip"), min_ev=p.get("min_ev", 0.04))
        chk = p["chk"]
        c = self._analyse(rec, p, chk)
        rec["checks"].append(c)
        rec["checks"] = rec["checks"][-14:]
        self._coverage(tag, rec["league"], c["pin"])
        text = self.report(rec)
        try:
            self._post(rec, text)
        except Exception as e:
            log.warning("report not posted: %s", e)
        self._wr(rk, rec)
        self._index(rk, rec.get("tip"))
        return {"ok": True}

    # ---------------------------------------------------------------- analysis of one check
    def _analyse(self, rec, p, chk):
        tag, thr = rec["tag"], float(rec.get("min_ev") or 0.04)
        soft, pin = p.get("soft") or {}, p.get("pin")
        prev = rec["checks"][-1] if rec["checks"] else None
        c = dict(label=chk.get("label"), kind=chk.get("kind"), t=chk.get("t"), status=chk.get("status"), age=chk.get("age"),
                 pin=bool(pin), nxt=chk.get("nxt"), engine_keys=chk.get("keys") or [], held=chk.get("held"))
        pf = ld.fair_table(pin) if pin else {}
        sf = soft
        rows = []
        if pin:
            for (mkt, line), d in pf.items():
                book = (sf.get(mkt) or {})
                if line is None:
                    odds_d = book if not any(isinstance(v, dict) for v in book.values()) else {}
                else:
                    odds_d = book.get("%g" % line) or {}
                for sel, pr in d.items():
                    if sel in odds_d:
                        rows.append(dict(mkt=mkt, line=line, sel=sel, odds=float(odds_d[sel]), p=pr, edge=edge(float(odds_d[sel]), pr),
                                         exact=True))
            lad = ld.ladder(tag, sf, pin)
            c["ladder_ok"], c["fit_err"] = lad["ok"], lad["err"]
            if lad["ok"]:
                known = {(r["mkt"], r["line"], r["sel"]) for r in rows}
                rows += [r for r in lad["rows"] if (r["mkt"], r["line"], r["sel"]) not in known]
        for r in rows:
            r["pkey"] = _pk(r["mkt"], r["line"], r["sel"])
        pp = (prev or {}).get("p") or {}
        c["e"] = {r["pkey"]: round(r["edge"], 4) for r in rows if r["edge"] > -0.03}
        c["p"] = {r["pkey"]: round(r["p"], 4) for r in rows if r["exact"]}
        for r in rows:
            r["toward"] = r["pkey"] in pp and r["p"] - pp[r["pkey"]] >= 0.01
        c["rows"] = sorted(rows, key=lambda r: -r["edge"])[:8] if rows else []
        c["rows"] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in c["rows"]]
        hist = {}
        for ch in rec["checks"]:
            for k, v in (ch.get("e") or {}).items():
                hist.setdefault(k, []).append(v)
        need = lambda r: thr if r["exact"] else thr * rules.MODEL_MULT()
        cands = sorted([r for r in rows if r["edge"] >= need(r)], key=lambda r: -r["edge"])
        c["verdict"], best, notes = rules.verdict(cands, hist, chk.get("age"), thr, chk.get("status") == "play") if pin else ("NO PRICE", None, [])
        c["notes"] = notes
        c["best"] = dict(pkey=best["pkey"], mkt=best["mkt"], line=best["line"], sel=best["sel"], odds=best["odds"], p=round(best["p"], 4),
                         edge=round(best["edge"], 4), exact=best["exact"]) if best else None
        if tag == "BB":                                   # basketball sends only its main lines: FinalPoint shows TheBeast's own call
            c["engine_rows"] = [dict(desc="%s %s %s" % (r.get("market"), r.get("selection"), r.get("line") if r.get("line") is not None else ""),
                                     odds=r.get("odds"), ev=r.get("ev")) for r in (chk.get("rows") or [])][:3]
            c["verdict"] = "THEBEAST PLAY NOW (no independent FinalPoint check for basketball yet)" if chk.get("status") == "play" else "NO BET"
        # the moneyline price of SportyBet at every check (drift) and what SportyBet has that Pinnacle has not
        ml = sf.get("1x2") or sf.get("ml") or {}
        c["ml"] = {k: v for k, v in ml.items() if not isinstance(v, dict)}
        c["only"] = sorted(k for k in sf if k not in (pin or {}))
        # shadow calculators (logged, not used)
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

    # ---------------------------------------------------------------- report
    def report(self, rec) -> str:
        e = html.escape
        tag, ch = rec["tag"], rec["checks"]
        c = ch[-1]
        home, away = rec.get("home") or "", rec.get("away") or ""
        streak = 0
        for x in reversed(ch):
            if x["pin"]:
                break
            streak += 1
        cov, n = self._coverage_of(tag, rec.get("league"))
        normal = (not any(x["pin"] for x in ch)) and cov < 0.5 and n >= 10
        lvl, lab = rules.fishy(streak, not any(x["pin"] for x in ch), c.get("kind") in LATE, cov, normal)
        verdict = c["verdict"]
        if tag == "BB" and c["pin"]:
            pass
        elif lvl >= 2:
            verdict = "FISHY - HUNT, DO NOT BET BLIND"
        elif lvl == 1:
            verdict = "WATCH (Pinnacle price missing 1 check)"
        elif not c["pin"]:
            verdict = "NO PRICE (reported only, never a bet)"
        L = ["%s <b>FINALPOINT · %s · %s vs %s</b>" % (ICON.get(tag, ""), tag, e(home), e(away)),
             "%s · %s" % (e(str(rec.get("league") or "")), _local(rec["tip"]).strftime("%a %d %b %H:%M") if rec.get("tip") else ""),
             "Check: %s · %s | FISHY LEVEL: %s" % (e(str(c.get("label"))), _local(c["t"]).strftime("%H:%M") if c.get("t") else "", e(lab)),
             SEP, "<b>Verdict: %s</b>" % e(verdict), ""]
        L.append("<b>Why:</b>")
        shown = 0
        bk = (c.get("best") or {}).get("pkey")
        for r in sorted(c["rows"], key=lambda r: (r["pkey"] != bk, -r["edge"])):
            need = rec.get("min_ev", 0.04) if r["exact"] else rec.get("min_ev", 0.04) * rules.MODEL_MULT()
            if r["edge"] < -0.0 and shown >= 1:
                break
            if shown >= 3:
                break
            gap = 100 * (r["edge"] - need)
            L.append("• %s @ %.2f · fair %.2f · edge %+.1f%% · %s%s" % (
                e(describe(r["mkt"], r["line"], r["sel"], home, away)), r["odds"], 1 / r["p"], 100 * r["edge"],
                ("%.1f points above the %.0f%% needed" % (gap, 100 * need)) if gap >= 0 else ("%.1f points short of the %.0f%% needed" % (-gap, 100 * need)),
                "" if r["exact"] else " · MODEL line (Pinnacle does not list it)"))
            shown += 1
        for r in c.get("engine_rows") or []:
            L.append("• TheBeast: %s @ %s · edge %s" % (e(str(r["desc"]).strip()), r.get("odds"), ("%+.1f%%" % (100 * r["ev"])) if r.get("ev") is not None else "?"))
        if not c["pin"]:
            L.append("• No Pinnacle price this check: nothing can be compared")
        for nline in c.get("notes") or []:
            L.append("• %s" % e(nline))
        tr = self._trend(rec)
        if tr:
            L.append("• Trend: %s" % e(tr))
        if c["pin"]:
            L += ["", "<b>Pinnacle view:</b>", "• %s" % e(self._view(c, home, away))]
        else:
            L += ["", "<b>Pinnacle view:</b>", "• Pinnacle MISSING - %d check%s in a row" % (streak, "s" if streak != 1 else "")]
        if lvl >= 2:
            L += ["", "<b>Hunt mode:</b>", "• SportyBet-only markets: %s" % (e(", ".join(c.get("only") or [])) or "none listed"),
                  "• Drift (SportyBet moneyline): %s" % e(self._drift(ch)),
                  "• Without a Pinnacle price there is no fair price, so no edge can be shown. Watch only."]
        sh = c.get("shadow") or []
        if sh:
            L.append("Shadow flags (logged, not used): %s" % e("; ".join(sh)))
        if c.get("ladder_ok") is False and c["pin"] and tag in ("FB", "IH", "AF", "HB"):
            L.append("Ladder: fit to Pinnacle too loose this check, only exact lines compared")
        if tag in ("TN", "VB", "BB") and c["pin"]:
            L.append("Exact lines only (no ladder for this sport yet)")
        L += ["", "TheBeast: %s" % ({"play": "PLAY NOW", "seen": "edge seen, waiting for a second check", "watch": "no pick",
                                    "nopin": "no Pinnacle price", "hold": "on hold"}.get(c.get("status"), str(c.get("status")))),
              SEP, "History: %s" % e(" · ".join("%s %s" % (x.get("label", "?").split(":")[0], x["verdict"]) for x in ch[-8:]))]
        return "\n".join(L)

    @staticmethod
    def _trend(rec):
        b = rec["checks"][-1].get("best")
        if not b:
            return ""
        seq = [x["e"].get(b["pkey"]) for x in rec["checks"] if x.get("e")]
        seq = [v for v in seq if v is not None]
        if len(seq) < 2:
            return ""
        d = seq[-1] - seq[0]
        word = "building" if d > 0.01 else ("fading" if d < -0.01 else "stable")
        return "edge %s: %s" % (word, " -> ".join("%+.1f%%" % (100 * v) for v in seq[-5:]))

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
