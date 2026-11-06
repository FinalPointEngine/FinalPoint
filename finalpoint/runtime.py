"""Starts the FinalPoint service. Selected by SPORT=finalpoint (api/server.py). It only receives the engines' feed (POST /fp/feed)
and posts one report per match to TELEGRAM_CHAT_ID (the FinalPoint group)."""
from __future__ import annotations

import logging

from config.settings import settings
from storage import repository as repo
from storage.db import init_db

log = logging.getLogger("finalpoint.runtime")


class FinalPointRuntime:
    def __init__(self):
        self.fp = None

    def start(self):
        init_db()
        from core import telegram_notify as tn
        from finalpoint.engine import FinalPoint
        chat = settings.TELEGRAM_CHAT_ID
        if not (settings.TELEGRAM_TOKEN and chat):
            log.warning("TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set: FinalPoint cannot post")
        self.fp = FinalPoint(repo.kv_get, repo.kv_set, lambda rec, text: tn.mirror_card(rec, text, chat=chat, title="FINALPOINT"))
        log.info("FinalPoint started")

    def feed(self, payload: dict) -> dict:
        return self.fp.feed(payload) if self.fp else {"ok": False, "why": "starting"}

    def stop(self):
        pass
