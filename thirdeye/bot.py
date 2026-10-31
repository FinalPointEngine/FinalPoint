"""ThirdEyeValidatorBot (long polling, owner-only)."""
from __future__ import annotations

import html
import logging
import time

from config.settings import settings
from core import telegram_notify as tn
from core.subscription import is_authorized
from thirdeye import cards, cfg
from thirdeye.engine import ICON

log = logging.getLogger("thirdeye.bot")
COMMANDS = [
    ("today", "Verdicts issued today"),
    ("open", "Open picks and their verdicts"),
    ("record", "Engine alone vs engine + ThirdEye (all sports)"),
    ("sport", "Record for one sport: /sport football"),
    ("match", "ThirdEye view of one match today: /match <team>"),
    ("graded", "Every graded pick with its verdict and reasons"),
    ("why", "Full reasoning for a pick: /why football:12"),
    ("clv", "Closing-line proxy of all picks"),
    ("sports", "Which engine databases are connected"),
    ("rules", "The ThirdEye rule set"),
    ("status", "ThirdEye health"),
    ("stop", "Pause verdicts"),
    ("start", "Resume verdicts"),
]
HELP = "ThirdEye commands: " + "  ".join("/%s" % c for c, _ in COMMANDS)


class Html(str):
    pass


class EyeBot:
    def __init__(self, eye):
        self.eye = eye
        self._running = False

    def handle_text(self, text):
        parts = text.strip().split(None, 1)
        if not parts:
            return HELP
        cmd = parts[0].lower().split("@")[0]
        fn = getattr(self, "cmd_" + cmd.lstrip("/"), None)
        if not fn or not cmd.startswith("/"):
            return HELP
        try:
            return fn(parts[1] if len(parts) > 1 else "")
        except Exception as e:
            log.exception("command failed")
            return "Error: %s" % e

    def cmd_start(self, a):
        self.eye.paused = False
        return HELP

    def cmd_stop(self, a):
        self.eye.paused = True
        return "ThirdEye paused. /start resumes it."

    def cmd_today(self, a):
        return Html(cards.today_card(self.eye.today()))

    def cmd_open(self, a):
        return Html(cards.open_card(self.eye.open_()))

    def cmd_record(self, a):
        return Html(cards.board_card("THIRDEYE RECORD", self.eye.board()))

    def cmd_sport(self, a):
        k = a.strip().lower()
        if k not in self.eye.readers:
            return "Use /sport " + " | ".join(self.eye.readers)
        return Html(cards.board_card("THIRDEYE RECORD: " + k.upper(), self.eye.board(k)))

    def cmd_match(self, a):
        if not a.strip():
            return "Use /match <team name>"
        hits = self.eye.find_match(a)
        if not hits:
            return "No match with that name on today's ThirdEye cards."
        out = []
        for sport, blk in hits[:3]:
            out.append(blk)
        return Html("\n\n".join(out))

    def cmd_graded(self, a):
        from thirdeye.rules import DONE
        vs = sorted([v for v in self.eye.st.v.values() if v["status"] in DONE], key=lambda v: v["seen"])[-15:]
        out = ["<b>GRADED PICKS</b> (last %d)" % len(vs)]
        for v in vs:
            out.append("%s %s:%s %s v %s | %s @ %.2f | %s %+.2f | %s %d" % (ICON[v["label"]], v["sport"], v["id"], html.escape(v["home"]), html.escape(v["away"]),
                       html.escape(v["desc"]), v["odds"], v["status"].upper(), v.get("ret") or 0, v["label"], v["score"]) + (" ⚠ " + v["bad"] if v.get("bad") else ""))
            out += ["   • " + html.escape(r) for r in v["reasons"][:3]]
        return Html("\n".join(out) if vs else "No graded picks yet.")

    def cmd_why(self, a):
        v = self.eye.find(a) if a.strip() else None
        if not v:
            return "Use /why <sport:id>, for example /why football:12"
        return Html(self.eye.alert_text(v, "REASONING"))

    def cmd_clv(self, a):
        b, c = self.eye.board(), self.eye.call_clv()
        out = []
        if b["clv"] is not None:
            out.append("Engine picks: closing-line proxy %+.1f%% over %d picks." % (b["clv"] * 100, b["clv_n"]))
        if c:
            out.append("ThirdEye's own calls: %+.1f%% over %d calls, %d of them positive." % (c["clv"] * 100, c["n"], c["pos"]))
        return "\n".join(out) or "No closing-line data yet."

    def cmd_sports(self, a):
        return Html(cards.sports_card(self.eye))

    def cmd_rules(self, a):
        return Html(cards.rules_card())

    def cmd_status(self, a):
        e = self.eye
        ok = sum(1 for s in e.status.values() if s and not s["error"])
        age = int(time.time() - e.last_cycle) if e.last_cycle else None
        return "ThirdEye: %d of %d engines readable, %d verdicts stored, last cycle %s%s." % (
            ok, len(e.readers), len(e.st.v), ("%ds ago" % age) if age is not None else "not yet", ", PAUSED" if e.paused else "")

    def process_update(self, u):
        if "message" in u and u["message"].get("text"):
            chat = u["message"]["chat"]["id"]
            if not settings.TELEGRAM_CHAT_ID:
                tn.send("Your chat id is <code>%s</code>. Put it in TELEGRAM_CHAT_ID and redeploy." % chat, chat_id=str(chat))
            elif is_authorized(chat):
                r = self.handle_text(u["message"]["text"])
                tn.send(r if isinstance(r, Html) else html.escape(r), chat_id=str(chat))

    def register_commands(self):
        try:
            tn.api("setMyCommands", commands=[dict(command=c, description=d[:250]) for c, d in COMMANDS])
        except Exception as e:
            log.warning("command menu not registered: %s", e)

    def run_forever(self):
        self._running = True
        self.register_commands()
        offset = None
        while self._running:
            try:
                res = tn.api("getUpdates", timeout=30, offset=offset, allowed_updates=["message"])
                for u in res.get("result", []):
                    offset = u["update_id"] + 1
                    try:
                        self.process_update(u)
                    except Exception:
                        log.exception("update failed")
            except Exception as e:
                log.warning("poll error: %s", e)
                time.sleep(5)

    def stop(self):
        self._running = False
