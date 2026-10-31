"""Read-only access to the 7 engine databases. Never writes. Any failure returns an error string, never raises."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from thirdeye import cfg

log = logging.getLogger("thirdeye.reader")
_ENGINES = {}


def _eng(url: str):
    if url not in _ENGINES:
        from sqlalchemy import create_engine
        from utils.dburl import normalize
        _ENGINES[url] = create_engine(normalize(url), pool_size=1, max_overflow=0, pool_pre_ping=True, pool_recycle=240,
                                      connect_args={"connect_timeout": 10})
    return _ENGINES[url]


def _epoch(x):
    if x is None:
        return 0.0
    if isinstance(x, datetime):
        return (x if x.tzinfo else x.replace(tzinfo=timezone.utc)).timestamp()
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _kv(conn, key):
    from sqlalchemy import text
    row = conn.execute(text("SELECT value FROM kv_store WHERE key=:k"), {"k": key}).fetchone()
    if not row or not row[0]:
        return None
    return json.loads(row[0])


class DbReader:
    """kv layout (football, tennis, hockey, american football, handball, volleyball) or the basketball picks table."""

    def __init__(self, key, label, prefix, url):
        self.key, self.label, self.prefix, self.url = key, label, prefix, url
        self.matches = []      # canonical matches from the last successful read (kv sports only)

    def connected(self):
        return bool(self.url)

    def read(self):
        """-> (picks list, error or None). Each pick is the canonical dict (see canon)."""
        if not self.url:
            return [], "not connected"
        try:
            with _eng(self.url).connect() as conn:
                return (self._basketball(conn) if self.key == "basketball" else self._kvsport(conn)), None
        except Exception as e:
            from utils.dburl import redact
            return [], redact(str(e))[:160]

    def _kvsport(self, conn):
        picks = (_kv(conn, self.prefix + "picks") or {}).get("picks", {})
        matches = _kv(conn, self.prefix + "matches") or {}
        out = []
        for p in picks.values():
            m = matches.get(p.get("mkey")) or {}
            out.append(canon_kv(self.key, p, m))
        self.matches = [canon_match(self.key, m) for m in matches.values() if isinstance(m, dict) and m.get("home")]
        return out

    def _basketball(self, conn):
        from sqlalchemy import text
        rows = conn.execute(text("SELECT id, created_at, league, home, away, commence, market, selection, line, fair_odds, min_odds, "
                                 "book_odds, ev, status, took_odds FROM picks WHERE mode <> 'sim' ORDER BY id DESC LIMIT 300")).fetchall()
        return [canon_bb(r) for r in rows]


def pkey(p):
    return "%s|%s|%s" % (p.get("mkt"), p.get("line") or "", p.get("sel"))


def canon_kv(sport, p, m):
    """Common record: id key sport lname home away tip created desc odds p fair ev status took ret hist(list of [t, p]) stale."""
    k = pkey(p)
    hist = []
    for c in (m.get("checks") or []):
        pm = c.get("pmap") or {}
        if k in pm and k in (c.get("keys") or []):
            hist.append([c.get("t") or 0, float(pm[k])])
    last = (m.get("checks") or [{}])[-1] if m.get("checks") else {}
    close = None
    pm = last.get("pmap") or {}
    if k in pm:
        close = float(pm[k])
    return dict(sport=sport, id=p["id"], key="%s:%s" % (sport, p["id"]), mkey=p.get("mkey"), lname=p.get("lname", ""), home=p.get("home", ""),
                away=p.get("away", ""), tip=_epoch(p.get("tip")), created=_epoch(p.get("created")), desc=p.get("desc", ""), odds=float(p.get("odds") or 0),
                p=float(p.get("p") or 0), fair=float(p.get("fair") or 0), ev=float(p.get("ev") or 0), status=p.get("status", "new"),
                took=p.get("took"), ret=p.get("ret"), settled=p.get("settled"), hist=hist, stale=bool(last.get("stale")), close=close,
                n_checks=len(m.get("checks") or []), min_odds=float(p.get("min_odds") or 0))


def canon_match(sport, m):
    checks = m.get("checks") or []
    last = checks[-1] if checks else {}
    why = last.get("why") or ""
    if isinstance(why, list):
        why = why[0] if why else ""
    return dict(sport=sport, key=m.get("key") or "%s|%s" % (m.get("home"), m.get("away")), lname=m.get("lname", ""), home=m.get("home", ""),
                away=m.get("away", ""), tip=_epoch(m.get("tip")), n_checks=len(checks), n_keys=len(last.get("keys") or []),
                stale=bool(last.get("stale")), why=str(why)[:60], skipped=bool(m.get("skipped")),
                checks=[dict(t=c.get("t") or 0, pmap=c.get("pmap") or {}, omap=c.get("omap") or {}) for c in checks[-8:] if c.get("pmap")],
                engine_keys=list(m.get("play_keys") or []))


def canon_bb(r):
    pid, created, league, home, away, commence, market, sel, line, fair, minodds, bodds, ev, status, took = r
    fair = float(fair or 0)
    odds = float(took or bodds or 0)
    ret = None
    if status == "won":
        ret = round(odds - 1, 4)
    elif status == "lost":
        ret = -1.0
    elif status == "void":
        ret = 0.0
    return dict(sport="basketball", id=pid, key="basketball:%s" % pid, mkey=None, lname=league or "", home=home or "", away=away or "",
                tip=_epoch(commence), created=_epoch(created), desc=("%s %s %s" % (market, sel, line if line is not None else "")).strip(),
                odds=float(bodds or 0), p=(1.0 / fair if fair > 1 else 0.0), fair=fair, ev=float(ev or 0), status=status or "new", took=took,
                ret=ret, hist=[], stale=False, close=None, n_checks=0, min_odds=float(minodds or 0))


def build_readers():
    return {k: DbReader(k, lab, pre, cfg.db_url(env)) for k, lab, pre, env in cfg.SPORTS}
