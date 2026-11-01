"""ThirdEye's own results history: finished games per sport per day, kept in its own database (one small kv row per sport-day)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from thirdeye import sources

HORIZON_DAYS = 45


def day_str(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


class Warehouse:
    def __init__(self, load, save, fetch=None, now=time.time, sports=None, pause=0.3):
        self._load, self._save, self.fetch, self._now, self.pause = load, save, fetch or sources.fetch_day, now, pause
        self.sports = sports or list(sources.SLUG)
        self.days = {}            # sport -> {date: rows}
        self.fetched = {}         # (sport, date) -> time
        self.fail = {}            # sport -> [count, retry_after]
        self.last_ok = {}
        self.last_err = {}
        self.version = 0
        self._loaded = set()

    def _key(self, sport, date):
        return "te_res:%s:%s" % (sport, date)

    def _dates(self, now):
        d0 = datetime.fromtimestamp(now, timezone.utc).date()
        return [(d0 - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(HORIZON_DAYS)]

    def _ensure(self, sport, now):
        if sport in self._loaded:
            return
        self._loaded.add(sport)
        d = self.days.setdefault(sport, {})
        for date in self._dates(now):
            try:
                raw = self._load(self._key(sport, date))
                if raw:
                    d[date] = json.loads(raw)
            except Exception:
                pass
        self.version += 1

    def tick(self, now=None):
        now = now or self._now()
        for sport in self.sports:
            self._ensure(sport, now)
            f = self.fail.get(sport, [0, 0])
            if f[1] > now:
                continue
            d = self.days[sport]
            dates = self._dates(now)
            todo = [x for x in dates[:2] if now - self.fetched.get((sport, x), 0) > 3600]           # today and yesterday: refresh hourly
            todo += [x for x in dates[2:] if x not in d][:2]                                     # older days: fill in, two per cycle
            for date in todo:
                try:
                    rows = self.fetch(sport, date)
                except Exception as e:
                    f = self.fail.setdefault(sport, [0, 0])
                    f[0] += 1
                    f[1] = now + min(3600, 120 * f[0])
                    self.last_err[sport] = str(e)[:100]
                    break
                self.fail[sport] = [0, 0]
                self.last_ok[sport] = now
                self.fetched[(sport, date)] = now
                if d.get(date) != rows:
                    d[date] = rows
                    self.version += 1
                    try:
                        self._save(self._key(sport, date), json.dumps(rows))
                    except Exception:
                        pass
                if self.pause:
                    time.sleep(self.pause)

    def games(self, sport):
        """All stored finished games of a sport, oldest first: (t, home, away, hg, ag)."""
        out = [(r[0], r[2], r[3], r[4], r[5]) for rows in list(self.days.get(sport, {}).values()) for r in rows]
        out.sort()
        return out

    def status(self, now=None):
        now = now or self._now()
        out = {}
        for s in self.sports:
            n = sum(len(v) for v in self.days.get(s, {}).values())
            out[s] = dict(days=len(self.days.get(s, {})), games=n, ok=self.last_ok.get(s), err=self.last_err.get(s), down=self.fail.get(s, [0, 0])[1] > now)
        return out
