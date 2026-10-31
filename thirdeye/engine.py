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
    def __init__(self, readers, load, save, send, now=time.time):
        self.readers, self.send, self._now = readers, send, now
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
        self.st.meta["last_cycle"] = now
        self.last_cycle = now
        self.st.flush()

    def _one(self, p, now, rec, open_by_match, open_n, backfill_ok):
        v = self.st.v.get(p["key"])
        done = p["status"] in DONE
        if v and v.get("locked"):
            if done and v["status"] != p["status"]:
                v.update(status=p["status"], ret=p["ret"])
            return
        live = (not done) and p["tip"] > now
        j_now = now if live else min(now, p["created"] or now)
        same = max(0, open_by_match.get(p["mkey"] or p["key"], 1) - 1)
        j = verdict.judge(p, j_now, rec, same, open_n)
        if v is None:
            v = dict(key=p["key"], sport=p["sport"], id=p["id"], lname=p["lname"], home=p["home"], away=p["away"], desc=p["desc"], odds=p["odds"],
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
        if done or not live:
            v["locked"] = True

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
               "Action: <b>%s</b> · pick #%s" % (STAKE[v["label"]], v["id"])]
        if v.get("tip"):
            out.append("Kick-off %s" % local(v["tip"]).strftime("%a %H:%M"))
        out += ["", "<b>Why</b>"] + ["• " + e(r) for r in v["reasons"][:6]]
        return "\n".join(out)

    # ---------------------------------------------------------------- scoreboard
    def board(self, sport=None):
        vs = [v for v in self.st.v.values() if sport in (None, v["sport"]) and v["status"] in DONE]
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
