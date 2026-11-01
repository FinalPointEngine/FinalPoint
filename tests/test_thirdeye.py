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


# ------------------------------------------------------------------ results history + stats
def sofa_ev(h, a, hs, as_, t, status="finished"):
    return {"status": {"type": status}, "homeScore": {"current": hs}, "awayScore": {"current": as_}, "startTimestamp": t, "tournament": {"name": "L"},
            "homeTeam": {"name": h}, "awayTeam": {"name": a}}


def test_parsers_and_source_fallback():
    from thirdeye import sources
    rows = sources.parse_sofa({"events": [sofa_ev("A", "B", 2, 1, 5), sofa_ev("C", "D", 0, 0, 6, "inprogress"), {"bad": 1}]})
    assert rows == [[5, "L", "A", "B", 2, 1]]
    espn = {"events": [{"date": "2026-10-30T23:00Z", "competitions": [{"status": {"type": {"completed": True}}, "competitors": [
        {"homeAway": "home", "team": {"displayName": "Celtics"}, "score": "110"}, {"homeAway": "away", "team": {"displayName": "Heat"}, "score": "100"}]}]}]}
    assert sources.parse_espn(espn)[0][2:] == ["Celtics", "Heat", 110, 100]

    def getter(url):
        if "sofascore" in url:
            raise RuntimeError("403")
        return espn
    assert sources.fetch_day("basketball", "2026-10-30", getter)[0][4] == 110          # SofaScore blocked: ESPN answers
    try:
        sources.fetch_day("handball", "2026-10-30", getter)
        assert False
    except RuntimeError:
        pass


def test_warehouse_backfills_persists_and_backs_off():
    from thirdeye.warehouse import Warehouse
    store, calls = {}, []

    def fetch(sport, date):
        calls.append((sport, date))
        return [[1, "L", "A", "B", 1, 0]]
    w = Warehouse(store.get, store.__setitem__, fetch, now=lambda: NOW, sports=["basketball"], pause=0)
    w.tick(NOW)
    assert len(calls) == 4 and len(store) == 4                       # today, yesterday + two older days
    w.tick(NOW + 10)
    assert len(calls) == 6                                           # two more older days; today/yesterday not refetched within the hour
    w2 = Warehouse(store.get, store.__setitem__, fetch, now=lambda: NOW, sports=["basketball"], pause=0)
    w2.tick(NOW + 20)
    assert len(w2.games("basketball")) >= 4                          # restart keeps the history

    def boom(sport, date):
        raise RuntimeError("blocked")
    w3 = Warehouse({}.get, {}.__setitem__, boom, now=lambda: NOW, sports=["tennis"], pause=0)
    w3.tick(NOW)
    st = w3.status(NOW)["tennis"]
    assert st["down"] and "blocked" in st["err"]
    n = len(w3.fail)
    w3.tick(NOW + 5)                                                  # backed off: not hammered again
    assert w3.fail["tennis"][0] == 1


def league():
    """Alpha is strong (wins by 10 at home or away); the rest are level."""
    teams = ["Alpha", "Beta", "Gamma", "Delta", "Echo", "Foxtrot"]
    games, t = [], NOW - 30 * 86400
    for d in range(30):
        for i in range(0, 6, 2):
            h, a = teams[(i + d) % 6], teams[(i + 1 + d * 2) % 6]
            if h == a:
                continue
            hg, ag = (110, 100) if h == "Alpha" else (100, 110) if a == "Alpha" else (101, 99) if d % 2 else (99, 101)
            games.append([int(t + d * 86400 + i * 3600), "L", h, a, hg, ag])
    return games


def test_stats_view_probabilities_and_extras():
    from thirdeye import stats
    from thirdeye.warehouse import Warehouse
    w = Warehouse({}.get, {}.__setitem__, lambda s, d: [], now=lambda: NOW, sports=["basketball"], pause=0)
    w._loaded.add("basketball")
    w.days["basketball"] = {"2026-01-01": league()}
    w.version += 1
    s = stats.Stats(w)
    v = s.view("basketball", "Alpha", "Beta", NOW)
    assert v and v["n_h"] >= 6 and v["exp_margin"] > 3
    assert stats.p_for_key(v, "h2h||home", "basketball") > 0.6 and stats.p_for_key(v, "h2h||away", "basketball") < 0.4
    assert stats.p_for_key(v, "total|205.5|over", "basketball") is not None and stats.p_for_key(v, "tennisx|1|home", "tennis") is None
    assert s.view("basketball", "Nobody FC", "Beta", NOW) is None                                  # no history, no opinion
    ex = stats.extras(v, "h2h||home", 0.75, 0.60, [])
    assert any(p > 0 and "ratings" in w_.lower() for p, w_ in ex)
    assert any(p < 0 for p, _ in stats.extras(v, "h2h||away", 0.25, 0.45, []))                       # backing the weaker side against the ratings
    assert stats.line(v).startswith("\U0001F4CA Form")


def test_stats_move_the_edge_and_show_on_the_card():
    from thirdeye import stats
    from thirdeye.warehouse import Warehouse
    w = Warehouse({}.get, {}.__setitem__, lambda s, d: [], now=lambda: NOW, sports=["basketball"], pause=0)
    w._loaded.add("basketball")
    w.days["basketball"] = {"2026-01-01": league()}
    w.version += 1
    s = stats.Stats(w)
    m = with_prices(match(1, sport="basketball", home="Alpha", away="Beta"))
    ck = dict(t=NOW - 600, pmap={"h2h||home": 0.55, "h2h||away": 0.45}, omap={"h2h||home": [1.7, 0.05], "h2h||away": [2.2, 0.05]})
    m["checks"] = [dict(ck, t=NOW - 1800), dict(ck, t=NOW - 1200), ck]
    from thirdeye import model
    plain = model.calls_for(m, NOW)
    seen = model.calls_for(m, NOW, 3, s.view("basketball", "Alpha", "Beta", m["tip"]))
    assert not plain and seen and seen[0]["key"] == "h2h||home"                                      # at 1.70 the prices alone show no edge; the ratings do
    assert seen[0]["p_stats"] is not None and any("ratings" in x.lower() for x in seen[0]["reasons"])


def test_card_shows_stats_line_and_sources_command():
    from thirdeye import stats
    from thirdeye.bot import EyeBot
    from thirdeye.warehouse import Warehouse
    w = Warehouse({}.get, {}.__setitem__, lambda s, d: [], now=lambda: NOW, sports=["basketball"], pause=0)
    w._loaded.add("basketball")
    w.days["basketball"] = {"2026-01-01": league()}
    w.version += 1
    m = with_prices(match(1, sport="basketball", home="Alpha", away="Beta"))
    ck = dict(t=NOW - 600, pmap={"h2h||home": 0.55, "h2h||away": 0.45}, omap={"h2h||home": [1.7, 0.05], "h2h||away": [2.2, 0.05]})
    m["checks"] = [dict(ck, t=NOW - 1800), dict(ck, t=NOW - 1200), ck]
    r = FakeReader("basketball", [])
    r.matches = [m]
    cards = FakeCards()
    store, sent = {}, []
    eye = Eye({"basketball": r}, store.get, store.__setitem__, sent.append, now=lambda: NOW, cards=cards, stats=stats.Stats(w))
    eye.wh = w
    eye.tick()
    card = cards.puts["te_day:basketball:1"]
    assert "\U0001F4CA Form" in card and "Alpha to win @ 1.70" in card
    assert "RESULT FEEDS" in EyeBot(eye).handle_text("/sources")


def test_integer_lines_use_the_same_key_as_the_engines():
    from thirdeye import reader
    assert reader.pkey({"mkt": "total", "line": 5.0, "sel": "under"}) == "total|5|under"
    assert reader.pkey({"mkt": "ah", "line": -1.5, "sel": "home"}) == "ah|-1.5|home" and reader.pkey({"mkt": "ml", "line": None, "sel": "home"}) == "ml||home"
    assert reader.pkey({"mkt": "ah", "line": 0.0, "sel": "home"}) == "ah|0|home"


def test_one_row_when_engine_and_thirdeye_pick_the_same_market():
    m = with_prices(match(1, sport="hockey", home="Dizel", away="Olimpiya"))
    ck = dict(t=NOW - 600, pmap={"total|5|under": 0.63, "total|5|over": 0.37}, omap={"total|5|under": [1.74, 0.05], "total|5|over": [2.0, 0.05]})
    m["checks"] = [dict(ck, t=NOW - 1800), dict(ck, t=NOW - 1200), ck]
    m["engine_keys"] = ["total|5|under"]
    p = pick(sport="hockey", key="hockey:1", mkey=m["key"], desc="Under 5 goals", odds=1.74, ev=0.096, home="Dizel", away="Olimpiya", pkey="total|5|under")
    r = FakeReader("hockey", [p])
    r.matches = [m]
    cards = FakeCards()
    eye, _, _ = mk({"hockey": r}, cards)
    eye.tick()
    card = cards.puts["te_day:hockey:1"]
    assert card.count("TE Yes | TB Yes") == 1 and "TE Yes | TB - | -" not in card


def test_sofascore_403_is_not_retried_and_new_espn_sources_parse():
    import time as _t
    from thirdeye import sources
    sources._blocked["until"] = 0.0
    calls = []

    def getter(url):
        calls.append(url)
        if "sofascore" in url:
            raise RuntimeError("HTTP Error 403: Forbidden")
        return {"events": [{"date": "2026-10-30T18:00Z", "name": "Paris", "groupings": [{"competitions": [
            {"date": "2026-10-30T18:00Z", "status": {"type": {"completed": True}}, "competitors": [
                {"order": 1, "winner": True, "athlete": {"displayName": "A Player"}}, {"order": 2, "winner": False, "athlete": {"displayName": "B Player"}}]},
            {"status": {"type": {"completed": True}}, "competitors": [{"order": 1, "team": {"displayName": "Doubles"}}]}]}]}]}
    rows = sources.fetch_day("tennis", "2026-10-30", getter)
    assert rows and all(r[1:] == ["Paris", "A Player", "B Player", 1, 0] for r in rows)       # doubles are ignored
    n = len(calls)
    sources.fetch_day("tennis", "2026-10-29", getter)
    assert not any("sofascore" in u for u in calls[n:])                  # blocked: skipped for hours
    assert sources.ESPN["football"] == ["soccer/all"]
    sources._blocked["until"] = 0.0
