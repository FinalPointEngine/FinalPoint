import json

from thirdeye import cfg, reader, rules, verdict
from thirdeye.cards import board_card, rules_card, sports_card
from thirdeye.engine import Eye

NOW = 1_800_000_000.0


def pick(**k):
    d = dict(sport="football", id=1, key="football:1", mkey="m1", lname="EPL", home="A", away="B", tip=NOW + 7200, created=NOW - 3600, desc="Over 2.5",
             odds=2.0, p=0.55, fair=1.8, ev=0.08, status="new", took=None, ret=None, hist=[[1, 0.55], [2, 0.55], [3, 0.56]], stale=False,
             close=0.56, n_checks=3, min_odds=1.9)
    d.update(k)
    return d


def test_canon_kv_history_and_close():
    p = dict(id=3, mkey="m", lname="L", home="H", away="A", tip=NOW, created=NOW, mkt="ou", line=2.5, sel="over", desc="o", odds=2.0, p=0.5, fair=1.9, ev=0.05, status="new")
    m = dict(checks=[dict(t=1, keys=["ou|2.5|over"], pmap={"ou|2.5|over": 0.5}), dict(t=2, keys=["ou|2.5|over"], pmap={"ou|2.5|over": 0.52})])
    c = reader.canon_kv("football", p, m)
    assert c["key"] == "football:3" and len(c["hist"]) == 2 and c["close"] == 0.52 and c["n_checks"] == 2


def test_good_pick_is_approved_and_bad_is_rejected():
    assert verdict.judge(pick(), NOW)["label"] == "APPROVE"
    bad = pick(ev=0.04, hist=[[1, 0.55]], close=0.50, n_checks=3, stale=True)
    assert verdict.judge(bad, NOW)["label"] in ("REJECT", "WATCH")
    assert verdict.judge(pick(ev=0.35), NOW)["label"] == "REJECT"
    assert verdict.judge(pick(p=0.2), NOW)["label"] == "REJECT"


def test_drift_down_reduces_score():
    a = verdict.judge(pick(hist=[[1, 0.55], [2, 0.52]]), NOW)["score"]
    b = verdict.judge(pick(), NOW)["score"]
    assert a < b


class FakeReader:
    def __init__(self, key, picks, err=None, conn=True):
        self.key, self.picks, self.err, self.conn = key, picks, err, conn
        self.label = key

    def connected(self):
        return self.conn

    def read(self):
        return self.picks, self.err


def mk(readers):
    store = {}
    sent = []
    eye = Eye(readers, store.get, store.__setitem__, sent.append, now=lambda: NOW)
    return eye, sent, store


def test_alert_once_and_scoreboard():
    r = FakeReader("football", [pick()])
    eye, sent, store = mk({"football": r})
    eye.tick()
    eye.tick()
    assert len(sent) == 1 and "APPROVE" in sent[0] and "FINAL VERDICT" in sent[0]
    # settle: won at 2.0
    r.picks = [pick(status="won", ret=1.0)]
    eye.tick()
    b = eye.board()
    assert b["n"] == 1 and b["eng_units"] == 1.0 and b["te_units"] == 1.0
    assert json.loads(store["te_verdicts"])["football:1"]["status"] == "won"
    assert "Engine alone" in board_card("T", b)


def test_backfill_is_silent_and_error_tolerated():
    old = pick(id=2, key="football:2", status="lost", ret=-1.0, tip=NOW - 9999)
    eye, sent, _ = mk({"football": FakeReader("football", [old]), "tennis": FakeReader("tennis", [], err="boom"),
                       "hockey": FakeReader("hockey", [], err="not connected", conn=False)})
    eye.tick()
    assert sent == [] and eye.board()["n"] == 1
    assert "ERROR boom" in sports_card(eye) and "not connected" in sports_card(eye)
    assert eye.status["tennis"]["error"] == "boom"


def test_rejected_pick_counts_as_held_back():
    eye, sent, _ = mk({"football": FakeReader("football", [pick(ev=0.35)])})
    eye.tick()
    eye.readers["football"].picks = [pick(ev=0.35, status="lost", ret=-1.0)]
    eye.tick()
    b = eye.board()
    assert b["rejected"] == 1 and b["rejected_ret"] == -1.0 and b["te_units"] == 0


def test_rules_card_and_thresholds():
    assert "APPROVE" in rules_card()
    assert cfg.approve() > cfg.reduce_() > cfg.watch()


def test_graded_command_lists_reasons():
    from thirdeye.bot import EyeBot
    r = FakeReader("football", [pick(ev=0.35, status="won", ret=1.0, tip=NOW - 99)])
    eye, _, _ = mk({"football": r})
    eye.tick()
    out = EyeBot(eye).handle_text("/graded")
    assert "REJECT" in out and "WON" in out and "Edge" in out


def test_late_pick_and_stale_open_picks_are_not_penalised():
    late = pick(tip=NOW + 300, hist=[], n_checks=0, close=None, ev=0.10)
    assert not any("10 minutes" in w for w in verdict.judge(late, NOW)["reasons"])
    old_open = [pick(id=i, key="football:%d" % i, mkey="x%d" % i, tip=NOW - 99999) for i in range(30)]
    r = FakeReader("football", old_open + [pick(id=99, key="football:99", mkey="m99")])
    eye, _, _ = mk({"football": r})
    eye.tick()
    assert not any("open picks" in w for w in eye.st.v["football:99"]["reasons"])
