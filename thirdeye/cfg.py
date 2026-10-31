"""ThirdEye settings (environment variables; defaults are the strict ones)."""
from __future__ import annotations

import os

SPORTS = [  # (key, label, kv prefix, env var holding that engine's read-only DATABASE_URL)
    ("basketball", "Basketball", "", "TE_DB_BASKETBALL"),
    ("football", "Football", "fb_", "TE_DB_FOOTBALL"),
    ("tennis", "Tennis", "tn_", "TE_DB_TENNIS"),
    ("hockey", "Ice hockey", "hk_", "TE_DB_HOCKEY"),
    ("afootball", "American football", "af_", "TE_DB_AMFOOTBALL"),
    ("handball", "Handball", "hb_", "TE_DB_HANDBALL"),
    ("volleyball", "Volleyball", "vb_", "TE_DB_VOLLEYBALL"),
]


def f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def approve() -> float:
    return f("TE_APPROVE", 65)


def reduce_() -> float:
    return f("TE_REDUCE", 50)


def watch() -> float:
    return f("TE_WATCH", 35)


def poll_sec() -> float:
    return f("TE_POLL_SEC", 90)


def tz_offset() -> float:
    return f("ALERT_TZ_OFFSET_HOURS", 1.0)


def digest_hour() -> int:
    return int(f("TE_DIGEST_HOUR", 8))


def db_url(env: str) -> str:
    return (os.environ.get(env) or "").strip()
