"""FinalPoint: takes every check an engine sends (core/fpfeed.py), keeps the match history, prices the whole market ladder and
posts ONE card per match to the group. The card grows: every check adds a block listing the markets seen at that check with a
call (POSSIBLE / WATCH / VALIDATED / AUDIT / FADING / WITHDRAWN); the best 3 are starred at every check; a market that holds on
two checks is VALIDATED so the reader need not wait for the later checks (which keep running and can WITHDRAW it)."""
from __future__ import annotations

import html
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from finalpoint import board, card, ladder as ld, results, rules
from finalpoint.rules import describe, edge

log = logging.getLogger("finalpoint")
ICON = {"FB": "⚽", "TN": "🎾", "IH": "🏒", "AF": "🏈", "HB": "🤾", "VB": "🏐", "BB": "🏀"}
SEP = "━" * 15
LATE = ("final", "t15", "last")
MAX_LIST = 5
def TOTAL_GAP():
    try:
        return float(os.environ.get("FP_TOTAL_GAP", "0.06"))
    except ValueError:
        return 0.06


def MIN_ODDS():
    try:
        return float(os.environ.get("FP_MIN_ODDS", "1.12"))
    except ValueError:
        return 1.12


BB_MKT = {"h2h": "ml", "spreads": "ah", "totals": "total"}


def _local(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc) + timedelta(hours=1)        # WAT


def _pk(mkt, line, sel):
    return "%s|%s|%s" % (mkt, "" if line is None else "%g" % line, sel)


def _label(x):
    x = str(x or "Check")
    return x if x.lower().startswith(("check", "final", "last")) else "Check " + x


class FinalPoint:
    def __init__(self, load, save, mirror, boards=None, chat=None):
        """load(key) -> str|None, save(key, str), mirror(rec, text) posts / edits the card of one match."""
        self._load, self._save, self._post = load, save, mirror
        self._boards, self._base = boards, board.link_base(chat)          # boards(texts, ids, hashes) -> (ids, hashes, ok), or None: off
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
        rec.update(home=card.disp(p.get("home")), away=card.disp(p.get("away")), league=p.get("league"), tip=p.get("tip"), min_ev=p.get("min_ev", 0.04))
        rec.setdefault("val", {})
        rec.setdefault("wd", {})
        prev = rec["checks"][-1] if rec["checks"] else None
        c = self._analyse(rec, p, p["chk"])
        rec["checks"].append(c)
        rec["checks"] = rec["checks"][-14:]
        fy = self._fishy(rec)
        c["fy"] = list(fy)
        top = c.pop("_top", [])
        c["story"] = card.story(rec, c, prev, fy, top)
        c["st"] = self._status(rec, c, fy[0], top)
        rec["top3"] = c.get("top3") or rec.get("top3") or []
        rec["picks"] = c.get("picks") or rec.get("picks") or []
        self._coverage(tag, rec["league"], c["pin"])
        text = card.render(rec)
        try:
            self._post(rec, text)
        except Exception as e:
            log.warning("card not posted: %s", e)
        self._wr(rk, rec)
        self._index(rk, rec.get("tip"))
        self._bd_touch(rk, rec)
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
    def _derived(self, tag, soft):
        """No Pinnacle price: fit the ladder to SportyBet's OWN moneyline and its most balanced total, then price its other lines.
        Shows only where SportyBet's lines disagree with each other. Rows are marked derived, never exact."""
        if tag not in ("FB", "IH", "AF", "HB") or not soft:
            return []
        pseudo = {}
        for k in ("1x2", "ml"):
            v = soft.get(k)
            if isinstance(v, dict) and v and not any(isinstance(x, dict) for x in v.values()):
                pseudo[k] = v
        best = None
        for ln, d in (soft.get("total") or {}).items():
            try:
                gap = abs(1 / float(d["over"]) - 1 / float(d["under"]))
            except Exception:
                continue
            if best is None or gap < best[0]:
                best = (gap, ln, d)
        if not pseudo or best is None:
            return []
        pseudo["total"] = {best[1]: best[2]}
        lad = ld.ladder(tag, soft, pseudo)
        if not lad["ok"]:
            return []
        out = []
        for r in lad["rows"]:
            r = dict(r, exact=False, derived=True)
            out.append(r)
        return out

    def _analyse(self, rec, p, chk):
        tag, thr = rec["tag"], float(rec.get("min_ev") or 0.04)
        half, mult = thr / 2, rules.MODEL_MULT()
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
        c["derived"] = False
        if not has_pin:
            d_rows = self._derived(tag, soft)
            if d_rows:
                rows, c["derived"] = d_rows, True
        rows = [r for r in rows if r.get("p") and 0.005 < r["p"] < 0.995]      # a model chance of 0% or 100% is no price at all
        for r in rows:
            r["pkey"] = _pk(r["mkt"], r["line"], r["sel"])
            r.setdefault("derived", False)
        pp = (prev or {}).get("p") or {}
        c["p"] = {r["pkey"]: round(r["p"], 4) for r in rows if r["exact"]}
        for r in rows:
            r["toward"] = r["pkey"] in pp and r["p"] - pp[r["pkey"]] >= 0.01
        hist = {}
        for ch in rec["checks"]:
            for k, v in (ch.get("e") or {}).items():
                hist.setdefault(k, []).append(v)
        c["e"] = {r["pkey"]: round(r["edge"], 4) for r in rows if r["edge"] >= half and not r["derived"]}
        home, away = rec.get("home") or "", rec.get("away") or ""
        tgap = {"over": 0.0, "under": 0.0}      # how far SportyBet's whole total sits from Pinnacle's (chance points): a constant offset is not an edge
        for ln, d in (soft.get("total") or {}).items():
            try:
                o, u = float(d["over"]), float(d["under"])
                pv = pf.get(("total", float(ln))) or {}
                if "over" in pv and "under" in pv:
                    so = (1 / o) / (1 / o + 1 / u)
                    tgap["over"] = max(tgap["over"], pv["over"] - so)
                    tgap["under"] = max(tgap["under"], pv["under"] - (1 - so))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
        c["tgap"] = {k: round(v, 3) for k, v in tgap.items()}
        shift_why = "SportyBet's whole total sits %.0f pts away from Pinnacle's on every line: check both quote the same market (overtime?) before trusting it"
        out = []
        for r in sorted(rows, key=lambda r: -r["edge"]):
            k, e = r["pkey"], r["edge"]
            own = r["exact"] and not r["derived"]               # priced by Pinnacle on this exact line
            need = thr if own else thr * mult                   # a line priced by a model needs more edge
            call, note = "", ""
            shifted = r["mkt"] == "total" and own and tgap.get(r["sel"], 0) >= TOTAL_GAP()
            if k in rec["val"] and not r["derived"] and shifted:
                rec["val"].pop(k, None)
                call, note = "POSSIBLE", "AUDIT: " + shift_why % (100 * tgap[r["sel"]])
            elif k in rec["val"] and not r["derived"]:
                if e >= half:
                    call = "VALIDATED (holds)"
                else:
                    call, note = "WITHDRAWN", "edge gone: %+.1f%%" % (100 * e)
                    rec["val"].pop(k, None)
                    rec["wd"][k] = c["label"]
            elif e >= need:
                why = []
                if r["derived"]:
                    why.append("NO PINNACLE, derived from SportyBet's own lines")
                elif not r["exact"]:
                    why.append("model-priced line, Pinnacle does not list it")
                elif e > rules.MAX_EDGE():
                    why.append("edge above %.0f%%, check it is the same market on both books" % (100 * rules.MAX_EDGE()))
                if shifted:
                    why.append(shift_why % (100 * tgap[r["sel"]]))
                if not fresh and not r["derived"]:
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
            out.append(dict(pkey=k, mkt=r["mkt"], line=r["line"], sel=r["sel"], d=describe(r["mkt"], r["line"], r["sel"], home, away),
                            odds=round(r["odds"], 2), fair=round(1 / r["p"], 2), e=round(e, 4), call=call, note=note, exact=r["exact"],
                            derived=r["derived"]))
        lo = {}                                                       # a line where one side pays almost nothing (Over 0.5 at 1.04) tells us nothing
        for o in out:
            g = (o["mkt"], o["line"])
            lo[g] = min(lo.get(g, 99.0), o["odds"])
        out = [o for o in out if lo[(o["mkt"], o["line"])] >= MIN_ODDS()]
        top = self._top3(out)
        for i, o in enumerate(top):
            o["star"] = i + 1
        c["top3"] = [o["pkey"] for o in top]
        c["picks"] = [{k: o[k] for k in ("pkey", "mkt", "line", "sel", "d", "odds", "fair", "e", "call", "exact", "derived")}
                      | {"tier": self._tier(o)} for o in top]
        c["_top"] = top
        c["tbl"] = self._table(tag, out, top, home, away)
        c["verdict"] = ("VALIDATED" if any(o["call"].startswith("VALIDATED") for o in out) else
                        "POSSIBLE" if any(o["call"] == "POSSIBLE" for o in out) else "NO VALUE") if (has_pin or c["derived"]) else "NO PRICE"
        c["wd_now"] = [o["d"] for o in out if o["call"] == "WITHDRAWN"]
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
    def _tier(o):
        """How far a pick can be trusted: 0 VALIDATED, 1 POSSIBLE on a Pinnacle line, 2 POSSIBLE model/derived, 3 closest-to-value (WATCH)."""
        if o["call"].startswith("VALIDATED"):
            return 0
        if o["call"] == "POSSIBLE":
            return 1 if (o["exact"] and not o["derived"]) else 2
        return 3

    def _top3(self, out):
        """The best 3 markets, always (when there is any price): validated first, then possible, then the closest to value.
        One side per line, and a market type is repeated only for a real (validated / possible) call."""
        cands = sorted([o for o in out if o["call"] != "WITHDRAWN"], key=lambda o: (self._tier(o), not (o["exact"] and not o["derived"]), -o["e"]))
        picked, seen, kinds, rest = [], set(), {}, []
        for o in cands:
            g = (o["mkt"], o["line"])
            if g in seen:
                continue
            if o["mkt"] in kinds:                          # one pick per market type: Under 4 and Under 4.5 are the same bet
                rest.append(o)
                continue
            seen.add(g)
            kinds[o["mkt"]] = self._tier(o)
            picked.append(o)
            if len(picked) == 3:
                return picked
        for o in rest:
            g = (o["mkt"], o["line"])
            if g not in seen and len(picked) < 3 and self._tier(o) == 3 and kinds.get(o["mkt"]) == 3:
                seen.add(g)
                picked.append(o)
        return picked

    @staticmethod
    def _table(tag, out, top, home, away):
        """The price table of one check: the line of every starred pick, plus the line closest to even for ml / spread / total."""
        by = {}
        for o in out:
            by.setdefault((o["mkt"], o["line"]), []).append(o)
        chosen = {(o["mkt"], o["line"]): True for o in top}                    # True = asked for by a pick
        for mk in ("ml", "1x2", "ah", "total"):
            if any(m == mk or (m in ("ml", "1x2") and mk in ("ml", "1x2")) for (m, _l) in chosen):
                continue
            cand = [(g, v) for g, v in by.items() if g[0] == mk]
            if cand:
                g, _v = min(cand, key=lambda gv: abs(1 / gv[1][0]["fair"] - 0.5))
                chosen.setdefault(g, False)
        groups = []
        star = {o["pkey"]: o["star"] for o in top}
        for g in sorted(chosen, key=lambda g: (card.ORDER.get(g[0], 3), g[1] if g[1] is not None else 0)):
            rows = []
            for o in sorted(by.get(g, []), key=lambda o: {"home": 0, "over": 0, "draw": 1}.get(o["sel"], 2)):
                tagtxt = o["call"].replace(" (holds)", "") if o["call"] else ""
                if o["pkey"] in star and not tagtxt:
                    tagtxt = "WATCH (closest)"
                mark = "derived" if o["derived"] else ("" if o["exact"] else "model")
                rows.append(dict(l=card.row_label(o["mkt"], o["line"], o["sel"], home, away, tag == "TN"), o=o["odds"], f=o["fair"], e=o["e"],
                                 s=o["pkey"] in star, t=tagtxt, m=mark))
            if rows:
                groups.append(dict(title=card.group_title(tag, g[0], not chosen[g]), rows=rows))
        return groups

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

    # ---------------------------------------------------------------- results
    def _finish(self, rec, hg, ag):
        """Grade the picks, add MATCH ENDED, update the running record (fp_stats, fp_ledger) and edit the card."""
        stats = self._rd("fp_stats", {})
        end, graded = results.end_block(rec["tag"], rec, hg, ag, stats)
        rec["end"] = end
        led = self._rd("fp_ledger", [])
        for g in graded:
            if g.get("status") != "ungraded":
                led.append(dict(tag=rec["tag"], key=g.get("pkey"), tier=g["tier"], status=g["status"], ret=g.get("ret", 0.0), t=end["at"]))
        self._wr("fp_stats", stats)
        self._wr("fp_ledger", led[-500:])
        try:
            self._post(rec, card.render(rec))
        except Exception as e:
            log.warning("result not posted: %s", e)
        self._wr("fp:%s:%s" % (rec["tag"], rec["key"]), rec)
        self._bd_touch("fp:%s:%s" % (rec["tag"], rec["key"]), rec)

    # ---------------------------------------------------------------- the two daily boards
    def _bd_touch(self, rk, rec):
        if not self._boards or not rec.get("tip"):
            return
        try:
            day = board.day_of(rec["tip"])
            st = self._rd("fp_bd:" + day, {"g": {}, "b1": [[], []], "b2": [[], []], "t": 0})
            old = st["g"].get(rk)
            e = board.entry(rk, rec, (old or {}).get("seen") or int(time.time()))
            st["g"][rk] = e
            st["dirty"] = True
            keys = lambda x: ([p.get("pkey") for p in x.get("val") or []], [p.get("pkey") for p in x.get("lead") or []])
            urgent = (old is None or old.get("st") != e["st"] or old.get("card") != e["card"] or old.get("end") != e.get("end")
                      or old.get("early") != e.get("early") or keys(old) != keys(e))
            self._bd_flush(day, st, urgent)
        except Exception as ex:
            log.warning("board not updated: %s", ex)

    def _bd_flush(self, day, st, urgent=False):
        if urgent:
            st["urg"] = True                                  # a new game / a status change: do not wait for the routine refresh
        gap = float(os.environ.get("FP_BOARD_MIN_SEC", "30")) if st.get("urg") else float(os.environ.get("FP_BOARD_SEC", "900"))
        now = time.time()
        if not st.get("dirty") or now - st.get("t", 0) < gap:
            self._wr("fp_bd:" + day, st)
            return
        ents = list(st["g"].values())
        rec2 = (self._rd("fp_stats", {}) or {}).get("VALIDATED") or {}
        b1, b2 = st["b1"], st["b2"]
        i2, h2, ok2 = self._boards(board.render_picks(day, ents, self._base, (b1[0] or [None])[0], rec2, now), b2[0], b2[1])
        st["b2"] = [i2, h2]
        i1, h1, ok1 = self._boards(board.render_games(day, ents, self._base, (i2 or [None])[0], now), b1[0], b1[1])
        st["b1"] = [i1, h1]
        if ok1 and ok2:
            st["dirty"], st["urg"] = False, False
            st["t"] = now
        self._wr("fp_bd:" + day, st)

    def board_tick(self, now=None) -> int:
        """Called every few minutes by the runtime: refresh today's and tomorrow's boards if something changed since the last edit."""
        if not self._boards:
            return 0
        now = now or time.time()
        n = 0
        with self._lock:
            for d in {board.day_of(now), board.day_of(now + 86400)}:
                st = self._rd("fp_bd:" + d, None)
                if st and st.get("dirty"):
                    self._bd_flush(d, st, False)
                    n += 1
        return n

    def settle_due(self, getter=None, now=None) -> int:
        """Look for the final score of every match that should be over. Called by the runtime every few minutes."""
        now = now or time.time()
        done = 0
        for rk, tip in list((self._rd("fp_index", {})).items()):
            if not tip or now < tip + results.EARLIEST or now > tip + results.LATEST:
                continue
            rec = self._rd(rk, None)
            if not rec or rec.get("end") or now - rec.get("end_try", 0) < results.RETRY:
                continue
            sc = results.find_score(rec["tag"], rec.get("home"), rec.get("away"), tip, getter)
            with self._lock:
                rec = self._rd(rk, None)
                if not rec or rec.get("end"):
                    continue
                if sc is None:
                    rec["end_try"] = now
                    self._wr(rk, rec)
                    continue
                self._finish(rec, sc[0], sc[1])
                done += 1
        return done

    def manual_result(self, home, away, hg, ag) -> str:
        """/result Home v Away 27-20: for matches the free feeds do not cover (handball, volleyball)."""
        same = results._same
        with self._lock:
            for rk, tip in (self._rd("fp_index", {})).items():
                rec = self._rd(rk, None)
                if not rec or rec.get("end"):
                    continue
                h, a = rec.get("home") or "", rec.get("away") or ""
                if same(h, home) and same(a, away):
                    self._finish(rec, hg, ag)
                    return "Saved: %s %d-%d %s. Card updated." % (h, hg, ag, a)
                if same(h, away) and same(a, home):
                    self._finish(rec, ag, hg)
                    return "Saved (teams were the other way round): %s %d-%d %s. Card updated." % (h, ag, hg, a)
        return "No open FinalPoint card matches those teams."

    # ---------------------------------------------------------------- card
    def report(self, rec) -> str:
        return card.render(rec)

    @staticmethod
    def _status(rec, c, lvl, top):
        if lvl >= 2:
            return "FISHY - HUNT, DO NOT BET BLIND"
        if not c["pin"] and not c.get("derived"):
            return "WATCH (Pinnacle price missing 1 check)" if lvl == 1 else "NO PRICE (nothing reliable to compare)"
        if c.get("wd_now"):
            return "WITHDRAWN: %s (edge gone)" % ", ".join(c["wd_now"][:2])
        val = [o for o in top if o["call"].startswith("VALIDATED")]
        if val:
            return "VALIDATED: %s @ %.2f" % (val[0]["d"], val[0]["odds"])
        poss = [o for o in top if o["call"] == "POSSIBLE"] or []
        if poss:
            return "POSSIBLE (%s)" % (poss[0]["note"] or "AUDIT")
        if not top:
            return "WATCH (no market to compare)"
        return "WATCH (no edge yet)" + (", NO PINNACLE: derived" if c.get("derived") else "")
