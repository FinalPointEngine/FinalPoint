"""ThirdEye engine: read the 7 engines, judge each pick once (re-judge until locked), keep the scoreboard."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from thirdeye import cfg, verdict
from thirdeye.rules import DONE
from thirdeye.store import Store

ICON = {"APPROVE": "✅", "REDUCE": "\U0001F7E1", "WATCH": "\U0001F440", "REJECT": "⛔"}
STAKE = {"APPROVE": "full stake", "REDUCE": "half stake", "WATCH": "wait: do not bet yet", "REJECT": "skip this one"}


def local(t):
    return datetime.fromtimestamp(t, timezone.utc) + timedelta(hours=cfg.tz_offset())


class Eye:
    def __init__(self, readers, load, save, send, now=time.time, cards=None):
        self.readers, self.send, self._now, self.cards = readers, send, now, cards
        self.st = Store(load, save)
        self.status = {}        # sport -> dict(picks=n, error=str|None, at=t)
        self.paused = False
        self.last_cycle = 0.0

    # ---------------------------------------------------------------- cycle
    def record(self, sport):
        rows = [v for v in self.st.v.values() if v["sport"] == sport and v["status"] in DONE]
        n = len(rows)
        if not n:
            return dict(n=0, roi=0.0)
        return dict(n=n, roi=sum((v.get("ret") or 0) for v in rows) / n)

    def tick(self):
        if self.paused:
            return
        now = self._now()
        fresh = first = not self.st.v
        for key, rd in self.readers.items():
            picks, err = rd.read()
            self.status[key] = dict(picks=len(picks), error=err, at=now)
            if err:
                continue
            open_by_match, open_n = {}, 0
            for p in picks:
                if p["status"] in ("new", "placed") and now < p["tip"] < now + 24 * 3600:   # only games still to come
                    open_n += 1
                    open_by_match[p["mkey"] or p["key"]] = open_by_match.get(p["mkey"] or p["key"], 0) + 1
            rec = self.record(key)
            for p in sorted(picks, key=lambda x: x["id"]):
                self._one(p, now, rec, open_by_match, open_n, backfill_ok=True)
            try:
                self._day_card(key, rd, now)
            except Exception:
                import logging
                logging.getLogger("thirdeye.engine").exception("day card %s failed", key)
        self.st.meta["last_cycle"] = now
        self.last_cycle = now
        self.st.flush()
        if self.cards:
            self.cards.flush()

    def _one(self, p, now, rec, open_by_match, open_n, backfill_ok):
        v = self.st.v.get(p["key"])
        done = p["status"] in DONE
        if v and v.get("locked"):
            if done and v["status"] != p["status"]:
                v.update(status=p["status"], ret=p["ret"])
            if done and not v.get("done_at"):
                self._mark_done(v, p, now)
            return
        live = (not done) and p["tip"] > now
        j_now = now if live else min(now, p["created"] or now)
        same = max(0, open_by_match.get(p["mkey"] or p["key"], 1) - 1)
        j = verdict.judge(p, j_now, rec, same, open_n)
        if v is None:
            v = dict(key=p["key"], mkey=p.get("mkey"), sport=p["sport"], id=p["id"], lname=p["lname"], home=p["home"], away=p["away"], desc=p["desc"], odds=p["odds"],
                     ev=p["ev"], tip=p["tip"], first=j["label"], seen=now, silent=not live)
            self.st.v[p["key"]] = v
            v.update(label=j["label"], score=j["score"], mult=j["mult"], reasons=j["reasons"], status=p["status"], ret=p["ret"], close=p["close"])
            if live:
                self._alert(v, p, "FINAL VERDICT")
        else:
            changed = j["label"] != v["label"]
            v.update(label=j["label"], score=j["score"], mult=j["mult"], reasons=j["reasons"], status=p["status"], ret=p["ret"], close=p["close"])
            if changed and live:
                self._alert(v, p, "VERDICT CHANGED")
        if done and not v.get("done_at"):
            self._mark_done(v, p, now)
        if done or not live:
            v["locked"] = True

    def _mark_done(self, v, p, now):
        """A result that arrives before the game even started is a data problem, never a graded pick."""
        v["done_at"] = now
        st = p.get("settled")
        if p["tip"] and ((st and st < p["tip"]) or now < p["tip"] - 60):
            v["bad"] = "graded before kick-off"

    def _alert(self, v, p, head):
        try:
            self.send(self.alert_text(v, head))
        except Exception:
            pass

    # ---------------------------------------------------------------- text
    def alert_text(self, v, head="FINAL VERDICT"):
        import html
        e = html.escape
        lab = dict((k, l) for k, l, _, _ in cfg.SPORTS).get(v["sport"], v["sport"])
        out = ["\U0001F441 <b>THIRDEYE · %s</b>" % head, "━" * 15,
               "%s <b>%s</b> · score %d/100" % (ICON[v["label"]], v["label"], v["score"]),
               "<b>%s</b> · %s" % (e(lab), e(v["lname"])),
               "%s v %s" % (e(v["home"]), e(v["away"])),
               "\U0001F3AF <b>%s</b> @ <b>%.2f</b> · engine edge %+.1f%%" % (e(v["desc"]), v["odds"], v["ev"] * 100),
               ("Action: <b>%s</b> · pick #%s" % (STAKE[v["label"]], v["id"])) if self._open(v) else
               ("Settled: <b>%s</b> %+.2f · pick #%s%s" % (v["status"].upper(), v.get("ret") or 0, v["id"], " · ⚠ " + v["bad"] if v.get("bad") else ""))]
        if v.get("tip"):
            out.append("Kick-off %s%s" % (local(v["tip"]).strftime("%a %H:%M"), "" if self._open(v) or v["tip"] > self._now() else " (played)"))
        out += ["", "<b>Why</b>"] + ["• " + e(r) for r in v["reasons"][:6]]
        return "\n".join(out)

    def _open(self, v):
        return v["status"] in ("new", "placed") and v["tip"] > self._now()

    # ---------------------------------------------------------------- scoreboard
    def board(self, sport=None):
        vs = [v for v in self.st.v.values() if sport in (None, v["sport"]) and v["status"] in DONE and not v.get("bad")]
        n = len(vs)
        eng = sum((v.get("ret") or 0) for v in vs)
        won = sum(1 for v in vs if v["status"] in ("won", "halfwon"))
        te = sum(v["mult"] * (v.get("ret") or 0) for v in vs)
        staked = sum(v["mult"] for v in vs)
        rej = [v for v in vs if v["mult"] == 0]
        rej_ret = sum((v.get("ret") or 0) for v in rej)
        clvs = [v["close"] * v["odds"] - 1 for v in self.st.v.values() if sport in (None, v["sport"]) and v.get("close") and v["odds"]]
        ap = [v for v in vs if v["label"] == "APPROVE"]
        return dict(n=n, won=won, eng_units=eng, eng_roi=(eng / n if n else 0.0), te_units=te, te_staked=staked,
                    te_roi=(te / staked if staked else 0.0), rejected=len(rej), rejected_ret=rej_ret, approved=len(ap),
                    approved_won=sum(1 for v in ap if v["status"] in ("won", "halfwon")),
                    clv=(sum(clvs) / len(clvs) if clvs else None), clv_n=len(clvs))

    def today(self):
        d = local(self._now()).strftime("%Y%m%d")
        return sorted([v for v in self.st.v.values() if not v.get("silent") and local(v["seen"]).strftime("%Y%m%d") == d], key=lambda v: v["seen"])

    def open_(self):
        now = self._now()
        return sorted([v for v in self.st.v.values() if v["status"] in ("new", "placed") and v["tip"] > now], key=lambda v: v["tip"])

    def find(self, ident):
        ident = ident.strip().lower()
        for k, v in self.st.v.items():
            if ident in (k, "%s:%s" % (v["sport"], v["id"])) or (ident.isdigit() and str(v["id"]) == ident):
                return v
        return None

    def digest(self):
        day = local(self._now()).strftime("%Y%m%d")
        if self.st.meta.get("digest") == day or local(self._now()).hour < cfg.digest_hour():
            return None
        self.st.meta["digest"] = day
        self.st.flush()
        return True

    # ---------------------------------------------------------------- daily match cards
    def match_line(self, m, now):
        """(sort time, text lines, bucket) for one match, from ThirdEye's own verdicts."""
        vs = [v for v in self.st.v.values() if v["sport"] == m["sport"] and v.get("mkey") == m["key"]]
        res_icon = {"won": "✅", "halfwon": "✅", "lost": "❌", "halflost": "❌", "void": "➖"}
        head = "%s %s v %s" % (local(m["tip"]).strftime("%H:%M"), m["home"][:19], m["away"][:19])
        subs, bucket = [], None
        for v in sorted(vs, key=lambda v: v["id"])[:3]:
            if v["status"] in DONE:
                subs.append("%s %s %+.2f · %s @ %.2f · ThirdEye said %s %d%s" % (res_icon.get(v["status"], "•"), v["status"].upper(), v.get("ret") or 0,
                            v["desc"][:30], v["odds"], v["label"], v["score"], " · ⚠ " + v["bad"] if v.get("bad") else ""))
                bucket = bucket or "done"
            else:
                subs.append("%s %s %d · %s @ %.2f" % (ICON[v["label"]], v["label"], v["score"], v["desc"][:30], v["odds"]))
                bucket = v["label"]
        if not subs:
            if m.get("skipped"):
                subs, bucket = ["⚪ skipped"], "none"
            elif m["tip"] < now:
                subs, bucket = ["▶ started, no bet"], "none"
            elif not m["n_checks"]:
                subs, bucket = ["⏳ not priced yet"], "wait"
            elif m["n_keys"]:
                subs, bucket = ["\U0001F440 edge forming (%d)" % m["n_keys"]], "forming"
            else:
                subs, bucket = ["⚪ no edge"], "none"
        return m["tip"], [head] + ["   " + x for x in subs], bucket

    def _day_card(self, sport, rd, now):
        if not self.cards or not rd.matches:
            return
        day = local(now).strftime("%Y%m%d")
        d = self.st.day.setdefault(day, {}).setdefault(sport, {})
        for m in rd.matches:
            if local(m["tip"]).strftime("%Y%m%d") == day:
                d[m["key"]] = dict(zip(("t", "lines", "bucket"), self.match_line(m, now)))
        for k in [k for k in self.st.day if k < day]:
            del self.st.day[k]
        rows = sorted(d.values(), key=lambda r: r["t"])
        if not rows:
            return
        cnt = {}
        for r in rows:
            cnt[r["bucket"]] = cnt.get(r["bucket"], 0) + 1
        lab = dict((k, l) for k, l, _, _ in cfg.SPORTS).get(sport, sport)
        pages = cards_pages(lab, local(now), rows, cnt)
        for i, text in enumerate(pages, 1):
            key = "te_day:%s:%d" % (sport, i)
            reply = self.cards.get("te_day:%s:%d" % (sport, i - 1)).get("msg_id") if i > 1 else None
            if reply:
                self.cards.put(key, text, day, True, None, reply)
            else:
                self.cards.put(key, text, day)
        for j in range(len(pages) + 1, int(self.st.meta.get("pages_" + sport, 0)) + 1):
            self.cards.put("te_day:%s:%d" % (sport, j), "(page %d: no matches)" % j, day)
        self.st.meta["pages_" + sport] = len(pages)

    def find_match(self, q):
        q = q.strip().lower()
        day = local(self._now()).strftime("%Y%m%d")
        out = []
        for sport, ms in self.st.day.get(day, {}).items():
            for r in ms.values():
                if q in " ".join(r["lines"]).lower():
                    out.append((sport, r))
        return out


def cards_pages(label, dt, rows, cnt, limit=3400):
    import html
    e = html.escape
    sym = [("APPROVE", "✅"), ("REDUCE", "\U0001F7E1"), ("WATCH", "\U0001F440"), ("REJECT", "⛔"), ("forming", "\U0001F440"), ("done", "\U0001F3C1"), ("none", "⚪"), ("wait", "⏳")]
    names = {"forming": "forming", "none": "no bet", "wait": "unpriced", "done": "settled"}
    tot = " ".join("%s%d %s" % (s, cnt[k], names.get(k, k.lower())) for k, s in sym if cnt.get(k))
    head = ["\U0001F441 <b>THIRDEYE · %s</b>" % e(label.upper()), "━" * 15, "<b>%s</b> · %d matches" % (dt.strftime("%a %d %b"), len(rows)), "<i>%s</i>" % tot, ""]
    pages, cur, size = [], list(head), sum(len(x) for x in head)
    for r in rows:
        block = "\n".join([("<b>%s</b>" % e(r["lines"][0]))] + [e(x) for x in r["lines"][1:]])
        if size + len(block) > limit and len(cur) > len(head if not pages else []):
            pages.append("\n".join(cur))
            cur, size = ["\U0001F441 <b>THIRDEYE · %s</b> (page %d)" % (e(label.upper()), len(pages) + 1), "━" * 15, ""], 50
        cur.append(block)
        size += len(block) + 1
    pages.append("\n".join(cur))
    return pages
