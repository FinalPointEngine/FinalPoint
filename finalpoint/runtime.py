"""Starts the FinalPoint service. Selected by SPORT=finalpoint (api/server.py). It only receives the engines' feed (POST /fp/feed)
and posts one report per match to TELEGRAM_CHAT_ID (the FinalPoint group)."""
from __future__ import annotations

import logging
import os
import re
import threading
import time

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
        self.fp = FinalPoint(repo.kv_get, repo.kv_set, lambda rec, text: tn.mirror_card(rec, text, chat=chat, title="FINALPOINT"),
                            boards=(lambda texts, ids, hs: tn.board_pages(chat, texts, ids, hs)) if (settings.TELEGRAM_TOKEN and chat and os.environ.get("FP_BOARDS", "1") != "0") else None,
                            chat=chat)
        threading.Thread(target=self._settle_loop, daemon=True, name="fp-settle").start()
        if os.environ.get("FP_RESULT_CMD", "1") != "0" and settings.TELEGRAM_TOKEN and chat:
            threading.Thread(target=self._cmd_loop, args=(chat,), daemon=True, name="fp-cmd").start()
        log.info("FinalPoint started")

    # results: look for final scores every few minutes (an asleep service is woken by the cron ping)
    def _settle_loop(self):
        time.sleep(90)
        while True:
            try:
                self.fp.board_tick()
                n = self.fp.settle_due()
                if n:
                    log.info("settled %d match card(s)", n)
            except Exception as e:
                log.warning("settle pass failed: %s", e)
            time.sleep(600)

    # "/result Home v Away 27-20" typed in the FinalPoint group: for matches no free feed covers (handball, volleyball)
    RESULT = re.compile(r"^/result(?:@\w+)?\s+(.+?)\s+(?:v|vs|-)\s+(.+?)\s+(\d+)\s*[-:]\s*(\d+)\s*$", re.I)

    def _cmd_loop(self, chat):
        from core import telegram_notify as tn
        offset = None
        while True:
            try:
                q = dict(timeout=25, allowed_updates=["message"])
                if offset is not None:
                    q["offset"] = offset
                for u in (tn.post_json(tn.API.format(token=settings.TELEGRAM_TOKEN, method="getUpdates"), q, timeout=40) or {}).get("result", []):
                    offset = u["update_id"] + 1
                    m = u.get("message") or {}
                    txt = (m.get("text") or "").strip()
                    if str((m.get("chat") or {}).get("id")) != str(chat) or not txt.lower().startswith("/result"):
                        continue
                    mt = self.RESULT.match(txt)
                    out = (self.fp.manual_result(mt.group(1), mt.group(2), int(mt.group(3)), int(mt.group(4))) if mt else
                           "Use: /result Home team v Away team 27-20")
                    tn.post_json(tn.API.format(token=settings.TELEGRAM_TOKEN, method="sendMessage"),
                                 dict(chat_id=chat, text=out, reply_to_message_id=m.get("message_id"), allow_sending_without_reply=True))
            except Exception as e:
                log.info("command poll: %s", str(e).replace(settings.TELEGRAM_TOKEN or "~", "***")[:120])
                time.sleep(15)

    def feed(self, payload: dict) -> dict:
        return self.fp.feed(payload) if self.fp else {"ok": False, "why": "starting"}

    def stop(self):
        pass
