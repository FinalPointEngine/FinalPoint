"""ThirdEye engine: read the 7 engines, judge each pick once (re-judge until locked), keep the scoreboard."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from thirdeye import cfg, model, verdict
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
        if v and not live:      # the verdict that was live at the time stands: never re-judge a game that has started or finished
            v.update(status=p["status"], ret=p["ret"], close=p["close"] or v.get("close"))
            if done and not v.get("done_at"):
                self._mark_done(v, p, now)
            v["locked"] = True
            return
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
        """No separate message per verdict: verdicts live on the one daily card per sport."""
        return

    def rec_of(self, v):
        """Card record of an engine pick with its ThirdEye verdict."""
        return dict(kind="engine", t=v["tip"], sport=v["sport"], id=v["id"], home=v["home"], away=v["away"], desc=v["desc"], odds=v["odds"], ev=v["ev"],
                    p=None, label=v["label"], score=v["score"], reasons=v["reasons"], status=v["status"], ret=v.get("ret"), bad=v.get("bad"))

    def alert_text(self, v, head="FINAL VERDICT"):
        """One verdict in the daily-card layout (used by /why)."""
        lab = dict((k, l) for k, l, _, _ in cfg.SPORTS).get(v["sport"], v["sport"])
        return "\n".join(["\U0001F441 <b>%s THIRDEYE · %s</b>" % (lab.upper(), head), "━" * 15, verdict_block(self.rec_of(v), None, self._now())])

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
    def own_call(self, m, now):
        """ThirdEye's own model call for a match; the first sighting of each selection is kept for CLV."""
        c = model.call_for(m, now)
        if not c:
            return None
        k = "%s:%s:%s" % (m["sport"], m["key"], c["key"])
        rec = self.st.calls.get(k)
        if rec is None:
            rec = self.st.calls[k] = dict(sport=m["sport"], desc=c["desc"], odds0=c["odds"], label0=c["label"], score0=c["score"], t0=now,
                                          tip=m["tip"], home=m["home"], away=m["away"])
        if m["tip"] > now and m["checks"]:
            pm = m["checks"][-1]["pmap"]
            if c["key"] in pm:
                rec["close"] = pm[c["key"]]
        return c

    def _items(self, sport, rd, now, day):
        """Everything ThirdEye says about today's matches of one sport: engine picks with its verdict, plus its own calls."""
        items = {}
        for v in self.st.v.values():
            if v["sport"] == sport and local(v["tip"]).strftime("%Y%m%d") == day:
                items["e:%s" % v["id"]] = self.rec_of(v)
        snap = self.st.day.setdefault(day, {}).setdefault(sport, {})
        for m in rd.matches:
            if local(m["tip"]).strftime("%Y%m%d") != day:
                continue
            if m["tip"] > now:
                c = self.own_call(m, now)
                if c and c["label"] != "REJECT" and c["key"] not in m.get("engine_keys", []):
                    snap["o:" + m["key"]] = dict(kind="own", t=m["tip"], sport=sport, id=None, home=m["home"], away=m["away"], desc=c["desc"],
                                                 odds=c["odds"], ev=c["ev"], p=c["p"], label=c["label"], score=c["score"], reasons=c["reasons"],
                                                 status="new", ret=None, bad=None)
                else:
                    snap.pop("o:" + m["key"], None)               # the call was withdrawn
        for k, r in snap.items():
            items[k] = r
        return items

    def _day_card(self, sport, rd, now):
        if not self.cards:
            return
        day = local(now).strftime("%Y%m%d")
        items = self._items(sport, rd, now, day)
        for k in [k for k in self.st.day if k < day]:
            del self.st.day[k]
        for k in [k for k, v in self.st.calls.items() if v["tip"] < now - 3 * 86400]:
            del self.st.calls[k]
        rows = sorted(items.values(), key=lambda r: (r["t"], r["kind"] != "engine", r["id"] or 0))
        if not rows:
            return
        lab = dict((k, l) for k, l, _, _ in cfg.SPORTS).get(sport, sport)
        pages = cards_pages(lab, local(now), rows, now)
        for i, text in enumerate(pages, 1):
            key = "te_day:%s:%d" % (sport, i)
            reply = self.cards.get("te_day:%s:%d" % (sport, i - 1)).get("msg_id") if i > 1 else None
            if reply:
                self.cards.put(key, text, day, True, None, reply)
            else:
                self.cards.put(key, text, day)
        for j in range(len(pages) + 1, int(self.st.meta.get("pages_" + sport, 0)) + 1):
            self.cards.put("te_day:%s:%d" % (sport, j), "(page %d: nothing to show)" % j, day)
        self.st.meta["pages_" + sport] = len(pages)
        self.st.meta["items_" + sport] = len(rows)

    def call_clv(self):
        cs = [v for v in self.st.calls.values() if v.get("close") and v["tip"] < self._now()]
        if not cs:
            return None
        return dict(n=len(cs), clv=sum(v["close"] * v["odds0"] - 1 for v in cs) / len(cs),
                    pos=sum(1 for v in cs if v["close"] * v["odds0"] - 1 > 0))

    def find_match(self, q):
        q = q.strip().lower()
        day = local(self._now()).strftime("%Y%m%d")
        out = []
        for sport, ms in self.st.day.get(day, {}).items():
            for r in ms.values():
                if q in (r["home"] + " " + r["away"]).lower():
                    out.append((sport, r))
        for v in self.st.v.values():
            if v["sport"] and local(v["tip"]).strftime("%Y%m%d") == day and q in (v["home"] + " " + v["away"]).lower():
                out.append((v["sport"], self.rec_of(v)))
        return out


SEP = "+" * 18
NAMES = {"APPROVE": "APPROVED", "REDUCE": "REDUCED", "WATCH": "WATCH", "REJECT": "REJECTED"}


def ordinal(n):
    return "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def verdict_block(r, n, now):
    """One pick in the daily-card layout."""
    import html
    e = html.escape
    out = ["%s <b>%s</b>: Score: %d/100" % (ICON[r["label"]], NAMES[r["label"]], r["score"])]
    if n is not None:
        out.append("<b>Pick %d:</b>" % n)
    out += [e(r["home"]), e(r["away"])]
    src = "engine edge" if r["kind"] == "engine" else "ThirdEye edge"
    out.append("\U0001F3AF <b>%s</b> @ <b>%.2f</b> · %s %+.1f%%" % (e(r["desc"]), r["odds"], src, r["ev"] * 100))
    who = ("engine pick #%s" % r["id"]) if r["kind"] == "engine" else "ThirdEye own call"
    if r["status"] in DONE:
        out.append("Result: <b>%s</b> %+.2f · %s%s" % (r["status"].upper(), r.get("ret") or 0, who, " · ⚠ " + e(r["bad"]) if r.get("bad") else ""))
    elif r["t"] and r["t"] < now:
        out.append("Played: result pending · %s" % who)
    else:
        out.append("Action: <b>%s</b> · %s" % (STAKE[r["label"]], who))
    out.append("Kick-off %s" % local(r["t"]).strftime("%a %H:%M"))
    out += ["", "<b>Why</b>"] + ["• " + e(x) for x in r["reasons"][:5]]
    return "\n".join(out)


def cards_pages(label, dt, rows, now, limit=3400):
    day = "%s, %s %s %d" % (dt.strftime("%A"), ordinal(dt.day), dt.strftime("%B"), dt.year)
    first = ["\U0001F441 <b>%s THIRDEYE · FINAL VERDICT: %s</b>" % (label.upper(), day), "━" * 15]
    pages, cur, size, n = [], list(first), 120, 0
    for r in rows:
        n += 1
        block = verdict_block(r, n, now) + "\n" + SEP
        if size + len(block) > limit and len(cur) > len(first):
            pages.append("\n".join(cur))
            cur, size = ["\U0001F441 <b>%s THIRDEYE · FINAL VERDICT</b> (page %d)" % (label.upper(), len(pages) + 1), "━" * 15], 80
        cur.append(block)
        size += len(block) + 1
    pages.append("\n".join(cur))
    return pages
