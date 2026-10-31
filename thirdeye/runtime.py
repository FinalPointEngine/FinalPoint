"""Starts the ThirdEye service. Selected by SPORT=thirdeye (api/server.py)."""
from __future__ import annotations

import logging
import threading

from config.settings import settings
from storage import repository as repo
from storage.db import init_db
from thirdeye import cards, cfg, reader, tg
from thirdeye.bot import EyeBot
from thirdeye.engine import Eye

log = logging.getLogger("thirdeye.runtime")


class ThirdEyeRuntime:
    def __init__(self):
        self.eye = None
        self.bot = None
        self._stop = threading.Event()

    def _loop(self):
        self._stop.wait(15)
        while not self._stop.is_set():
            try:
                self.eye.tick()
                if self.eye.digest():
                    tg.plain(cards.board_card("THIRDEYE DAILY RECORD", self.eye.board()), silent=True)
            except Exception:
                log.exception("cycle failed")
            self._stop.wait(cfg.poll_sec())

    def start(self):
        init_db()
        self.eye = Eye(reader.build_readers(), repo.kv_get, repo.kv_set, lambda t: tg.plain(t))
        threading.Thread(target=self._loop, name="te-loop", daemon=True).start()
        if settings.TELEGRAM_TOKEN:
            self.bot = EyeBot(self.eye)
            threading.Thread(target=self.bot.run_forever, name="te-bot", daemon=True).start()
        else:
            log.warning("TELEGRAM_TOKEN not set: ThirdEye alerts and commands disabled")
        log.info("ThirdEye started")

    def stop(self):
        self._stop.set()
        if self.bot:
            self.bot.stop()
