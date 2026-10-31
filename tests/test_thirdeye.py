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
        self.matches = []

    def connected(self):
        return self.conn

    def read(self):
        return self.picks, self.err


class FakeCards:
    def __init__(self):
        self.puts = {}

    def get(self, k):
        return {"msg_id": 1} if k in self.puts else {}

    def put(self, key, text, day, silent=True, data=None, reply_to=None):
        self.puts[key] = text

    def flush(self):
        pass


def mk(readers, cards=None):
    store = {}
    sent = []
    eye = Eye(readers, store.get, store.__setitem__, sent.append, now=lambda: NOW, cards=cards)
    return eye, sent, store


def test_alert_once_and_scoreboard():
    r = FakeReader("football", [pick()])
    cards = FakeCards()
    eye, sent, store = mk({"football": r}, cards)
    eye.tick()
    eye.tick()
    assert sent == []                                                      # no separate message per verdict
    card = cards.puts["te_day:football:1"]
    assert "THIRDEYE PRIVATE · NEON ENGINE OUTPUT" in card and "SPORT: FOOTBALL" in card and "THIRDEYE MARKETS:" in card and "THEBEAST MARKETS:" in card
    assert "COMPARISON:" in card and "FINAL:" in card and "AGREED" in card
    # settle: won at 2.0
    r.picks = [pick(status="won", ret=1.0, tip=NOW - 7200)]
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
    eye.readers["football"].picks = [pick(ev=0.35, status="lost", ret=-1.0, tip=NOW - 7200)]
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


def match(i, **k):
    d = dict(sport="football", key="m%d" % i, lname="EPL", home="Home%d" % i, away="Away%d" % i, tip=NOW + 3600 + i * 60, n_checks=2, n_keys=0,
             stale=False, why="", skipped=False)
    d.update(k)
    return d


def with_prices(m, over=2.0):
    ck = dict(t=NOW - 600, pmap={"total|2.5|over": 0.55, "total|2.5|under": 0.45}, omap={"total|2.5|over": [over, 0.05], "total|2.5|under": [1.8, 0.05]})
    m["checks"] = [dict(ck, t=NOW - 1800), dict(ck, t=NOW - 1200), ck]
    m["engine_keys"] = []
    return m


def test_own_model_blend_and_call():
    from thirdeye import model
    pr = model.own_probs({"total|2.5|over": [2.0, 0.05], "total|2.5|under": [1.8, 0.05]}, {"total|2.5|over": 0.55, "total|2.5|under": 0.45})
    pin, sp, te = pr["total|2.5|over"]
    assert pin == 0.55 and 0.47 < sp < 0.48 and pin > te > sp
    c = model.call_for(with_prices(match(1)), NOW)
    assert c and c["key"] == "total|2.5|over" and c["desc"] == "Over 2.5" and 0.03 <= c["ev"] <= 0.15
    assert model.call_for(with_prices(match(1, tip=NOW - 5)), NOW) is None           # already started
    assert model.call_for(with_prices(match(1), over=1.6), NOW) is None             # no edge at 1.60
    assert model.describe("ah|-1.5|home", "A", "B") == "A -1.5" and model.describe("1x2||away", "A", "B") == "B to win"


def test_day_card_shows_own_calls_not_the_no_bet_list_and_splits_pages():
    r = FakeReader("football", [pick(mkey="m0")])
    r.matches = [with_prices(match(i)) for i in range(1, 121)] + [match(500 + i) for i in range(30)]     # 30 matches with no call
    cards = FakeCards()
    eye, _, _ = mk({"football": r}, cards)
    eye.tick()
    assert len(cards.puts) >= 2                                   # 120 calls do not fit one card
    page1 = cards.puts["te_day:football:1"]
    assert "Over 2.5 @ 2.00 · Score" in page1 and "TE Yes | TB - | -" in page1 and "no edge" not in page1 and "FINAL: Play ThirdEye markets. TheBeast: no pick." in page1
    assert "page 2" in cards.puts["te_day:football:2"]
    assert all(len(x) < 4000 for x in cards.puts.values())
    assert eye.find_match("home1")
    assert eye.call_clv() is None or eye.call_clv()["n"] >= 0


def test_result_before_kickoff_is_flagged_and_kept_out_of_the_record():
    r = FakeReader("football", [pick(status="won", ret=1.0, tip=NOW + 600)])     # won 10 minutes BEFORE kick-off
    eye, _, _ = mk({"football": r})
    eye.tick()
    v = eye.st.v["football:1"]
    assert v["bad"] and eye.board()["n"] == 0
    from thirdeye.bot import EyeBot
    assert "graded before kick-off" in EyeBot(eye).handle_text("/graded")


def test_settled_pick_text_has_no_action_line():
    r = FakeReader("football", [pick(status="won", ret=1.2, tip=NOW - 7200, settled=NOW - 100)])
    eye, _, _ = mk({"football": r})
    eye.tick()
    txt = eye.alert_text(eye.st.v["football:1"], "REASONING")
    assert "Result:" in txt and "WON" in txt and "do not bet yet" not in txt and "Action:" not in txt


def test_clv_is_not_double_counted_in_the_score():
    a = verdict.judge(pick(close=0.70), NOW)["score"]
    b = verdict.judge(pick(close=0.40), NOW)["score"]
    assert a == b


def test_verdict_given_before_the_game_is_not_rewritten_afterwards():
    r = FakeReader("football", [pick()])
    eye, _, _ = mk({"football": r})
    eye.tick()
    before = (eye.st.v["football:1"]["label"], eye.st.v["football:1"]["score"])
    r.picks = [pick(status="lost", ret=-1.0, tip=NOW - 7200, hist=[], close=None)]
    eye.tick()
    v = eye.st.v["football:1"]
    assert (v["label"], v["score"]) == before and v["status"] == "lost"


def test_card_layout_matches_the_agreed_format():
    p1 = pick(sport="handball", key="handball:1", desc="Under 62.5 goals", odds=1.9, ev=0.073, home="TV Grosswallstadt", away="VfL Potsdam", mkey="h1",
              pkey="total|62.5|under")
    r = FakeReader("handball", [p1])
    m = match(1, sport="handball", key="h1", home="TV Grosswallstadt", away="VfL Potsdam")
    ck = dict(t=NOW - 600, pmap={"total|62.5|under": 0.56, "total|62.5|over": 0.44}, omap={"total|62.5|under": [1.9, 0.05], "total|62.5|over": [1.9, 0.05]})
    m["checks"] = [dict(ck, t=NOW - 1800), dict(ck, t=NOW - 1200), ck]
    m["engine_keys"] = ["total|62.5|under"]
    r.matches = [m]
    cards = FakeCards()
    eye, _, _ = mk({"handball": r}, cards)
    eye.tick()
    card = cards.puts["te_day:handball:1"]
    for part in ("THIRDEYE PRIVATE · NEON ENGINE OUTPUT", "SPORT: HANDBALL", "TV Grosswallstadt vs VfL Potsdam", "Kick-off:", "THIRDEYE MARKETS:", "THEBEAST MARKETS:",
                 "Under 62.5 goals @ 1.90 · Edge +7.3%", "COMPARISON:", "TE Yes | TB Yes | AGREED", "FINAL: Play ThirdEye markets. TheBeast: agreed."):
        assert part in card, part
    from thirdeye.engine import ordinal
    assert [ordinal(n) for n in (1, 2, 3, 11, 22, 31)] == ["1st", "2nd", "3rd", "11th", "22nd", "31st"]


def test_tiers_and_disagreement():
    eye, _, _ = mk({})
    assert eye.tier(80) == ("AGREED", "STRONG") and eye.tier(60) == ("AGREED", "WEAK") and eye.tier(45)[0] == "INDIFFERENT" and eye.tier(20)[0] == "DISAGREED"
    assert verdict.judge(pick(ev=0.049), NOW)["score"] >= 55            # a real 4.9% edge is not rejected
    assert verdict.judge(pick(ev=-0.02), NOW)["score"] < 40             # a negative edge is disagreed
