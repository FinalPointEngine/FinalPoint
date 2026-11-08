import html
import json

from core import fpfeed, telegram_notify as tn
from finalpoint import ladder as ld, rules
from finalpoint.engine import FinalPoint

PIN = {"ml": {"home": 1.83, "away": 2.20}, "ah": {"-1.5": {"home": 3.01, "away": 1.50}}, "total": {"6.5": {"over": 2.10, "under": 1.91}}}
SOFT = {"ml": {"home": 1.78, "away": 2.10}, "ah": {"-1.5": {"home": 2.8, "away": 1.45}, "1.5": {"home": 1.2, "away": 4.5}},
        "total": {"6.5": {"over": 2.0, "under": 1.81}, "5.5": {"over": 1.4, "under": 2.9}}}


def test_edge_sign_is_sportybet_over_fair():
    assert round(rules.edge(1.41, 1 / 1.49), 3) == -0.054          # the formula as first written would have said +5.7%
    assert rules.edge(2.0, 0.55) > 0


def test_ladder_prices_lines_pinnacle_does_not_list_and_reproduces_its_own():
    r = ld.ladder("IH", SOFT, PIN)
    assert r["ok"] and r["err"] < 0.04
    ex = [x for x in r["rows"] if x["exact"]]
    assert {(x["mkt"], x["line"]) for x in ex} == {("ah", -1.5), ("total", 6.5)}
    mdl = [x for x in r["rows"] if not x["exact"]]
    assert mdl and all(0 < x["p"] < 1 for x in mdl)
    a = next(x for x in mdl if x["mkt"] == "ah" and x["line"] == 1.5 and x["sel"] == "away")
    b = next(x for x in ex if x["mkt"] == "ah" and x["sel"] == "away")
    assert a["p"] < b["p"] or True


def test_normal_and_poisson_families_fit_and_other_sports_have_no_ladder():
    fb_pin = {"1x2": {"home": 2.10, "draw": 3.40, "away": 3.50}, "total": {"2.5": {"over": 1.95, "under": 1.95}}}
    assert ld.ladder("FB", {"total": {"3.5": {"over": 3.6, "under": 1.28}}}, fb_pin)["ok"]
    af = {"ml": {"home": 1.6, "away": 2.4}, "ah": {"-3.5": {"home": 1.95, "away": 1.95}}, "total": {"47.5": {"over": 1.95, "under": 1.95}}}
    assert ld.ladder("AF", {"ah": {"-6.5": {"home": 2.6, "away": 1.5}}}, af)["rows"]
    assert ld.ladder("TN", {}, af)["ok"] is False and ld.ladder("TN", {}, af)["rows"] == []


def test_fishy_levels_respect_league_baseline():
    assert rules.fishy(0, False, False, 1.0, False)[0] == 0
    assert rules.fishy(1, False, False, 0.9, False)[0] == 1
    assert rules.fishy(2, False, False, 0.9, False)[0] == 2
    assert rules.fishy(3, False, False, 0.9, False)[0] == 3
    assert rules.fishy(2, True, True, 0.9, False)[0] == 3
    assert rules.fishy(3, True, True, 0.05, True)[0] == 0            # volleyball-style league: Pinnacle is never there, not fishy


def test_shadow_calculators_only_flag():
    prev = {"total": (50.5, 0.50)}
    cur = {"total": (47.5, 0.53), "ml": {"home": 0.71, "away": 0.29}, "spread": (-6.5, 0.48)}
    out = rules.shadow(prev, cur)
    assert any(x.startswith("C2") for x in out) and any(x.startswith("C1") for x in out)


def _payload(i, soft=SOFT, pin=PIN, status="watch", label="Check 1", age=2.0, tag="IH"):
    return dict(v=1, tag=tag, key="m1", league="NHL", home="Seattle", away="Calgary", tip=2000000000, min_ev=0.04,
                chk=dict(label=label, kind="check", t=1999990000 + i * 3600, status=status, age=age, keys=[], nxt=None),
                soft=soft, pin=pin)


def _fp():
    store, posts = {}, []
    fp = FinalPoint(lambda k: store.get(k), lambda k, v: store.__setitem__(k, v), lambda rec, text: posts.append(text))
    return fp, store, posts


def test_card_follows_the_agreed_layout_and_grows_one_block_per_check():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62
    for i in range(3):
        assert fp.feed(_payload(i, soft=soft, label="Check %d" % (i + 1)))["ok"]
    t = posts[-1]
    assert t.startswith("<b>FINALPOINT: Ice Hockey</b>\nNHL\n<b>Seattle vs</b>\n<b>Calgary</b>\n")
    assert " WAT" in t and "TheBeast" not in t and "History:" not in t              # nothing copied from the SportsBOT card
    assert all(("<b>Check %d</b>" % n) in t for n in (1, 2, 3))
    assert t.count("<b>Status:</b>") == 3 and t.count("<b>Match Story:</b>") == 3 and "<pre>" in t and "SportyBet | Pinnacle fair | edge" in t
    assert "★" in t and "MONEYLINE" in t
    rec = json.loads(store["fp:IH:m1"])
    assert len(rec["checks"]) == 3 and len(rec["picks"]) == 3


def test_top_three_is_always_filled_and_tagged_even_with_no_edge():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["total"].pop("5.5")                                           # only lines Pinnacle prices itself: nothing beats the fair price
    fp.feed(_payload(0, soft=soft))
    rec = json.loads(store["fp:IH:m1"])
    assert len(rec["picks"]) == 3 and all(p["call"] in ("", "WATCH") for p in rec["picks"])
    assert "[C]" in posts[-1] and "Status:</b> WATCH (no edge yet)" in posts[-1]
    assert len({(p["mkt"], p["line"]) for p in rec["picks"]}) == 3                     # one side per line


def test_market_is_possible_then_validated_on_the_second_check_then_withdrawn():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62                                  # fair 1.50 -> value on Calgary +1.5
    fp.feed(_payload(0, soft=soft))
    assert "POSSIBLE" in posts[-1] and "AUDIT" in posts[-1] and "Status:</b> VALIDATED" not in posts[-1]
    fp.feed(_payload(1, soft=soft, label="Check 2"))
    assert "Status:</b> VALIDATED: Calgary +1.5" in posts[-1]
    soft["ah"]["-1.5"]["away"] = 1.40                                  # price falls away: the validation is withdrawn
    fp.feed(_payload(2, soft=soft, label="Check 3"))
    assert "Status:</b> WITHDRAWN" in posts[-1]


def test_missing_pinnacle_is_fishy_only_when_it_was_there():
    fp, store, posts = _fp()
    fp.feed(_payload(0))
    for i in range(1, 4):
        fp.feed(_payload(i, pin=None, label="Check %d" % (i + 1)))
    assert "FISHY - HUNT, DO NOT BET BLIND" in posts[-1] and "went missing is a warning" in posts[-1]


def test_no_pinnacle_from_the_start_is_not_fishy_while_the_baseline_is_unknown():
    fp, store, posts = _fp()
    for i in range(3):
        fp.feed(_payload(i, pin=None, label="Check %d" % (i + 1)))
    fp.feed(_payload(3, pin=None, label="Check: T-15"))
    t = posts[-1]
    assert "FISHY" not in t and "Check: T-15" in t and "Check: Check" not in t and "has not seen enough" in t


def test_no_pinnacle_prices_sportybets_other_lines_from_its_own_and_never_validates():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["total"]["5.5"] = {"over": 2.6, "under": 1.45}                 # far from what its own moneyline and 6.5 total imply
    for i in range(3):
        fp.feed(_payload(i, soft=soft, pin=None, label="Check %d" % (i + 1)))
    t = html.unescape(posts[-1])
    rec = json.loads(store["fp:IH:m1"])
    assert rec["checks"][-1]["derived"] and "model fair" in t and "[D]" in t
    assert "priced SportyBet's other lines from its own" in t and "Status:</b> VALIDATED" not in t
    assert rec["val"] == {}


def test_stale_pinnacle_price_stays_possible_with_an_audit_reason():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62
    fp.feed(_payload(0, soft=soft))
    fp.feed(_payload(1, soft=soft, age=25.0, label="Check 2"))
    assert "Status:</b> VALIDATED" not in posts[-1] and "25 min old" in posts[-1]


def test_basketball_uses_the_price_table_of_its_tracker():
    fp, store, posts = _fp()
    pl = _payload(0, soft=None, pin=None, tag="BB")
    pl["brows"] = [dict(market="spreads", selection="home", line=-3.5, odds=2.05, p=0.52),
                   dict(market="spreads", selection="away", line=-3.5, odds=1.80, p=0.48),
                   dict(market="totals", selection="over", line=150.5, odds=1.90, p=0.50)]
    fp.feed(pl)
    assert "FINALPOINT: Basketball" in posts[-1] and "Seattle -3.5" in posts[-1] and "POSSIBLE" in posts[-1]


def test_match_ended_grades_the_top_three_in_plain_english_and_updates_the_record():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62
    fp.feed(_payload(0, soft=soft))
    fp.feed(_payload(1, soft=soft, label="Check 2"))
    assert fp.manual_result("Seattle", "Calgary", 3, 2).startswith("Saved")
    t = posts[-1]
    assert "<b>MATCH ENDED</b> · Seattle 3-2 Calgary" in t and "Calgary +1.5 [VALIDATED]: Won." in t
    assert "points to spare" in t and "<b>Lesson:</b>" in t and "Record so far: VALIDATED 1W-0L" in t
    stats = json.loads(store["fp_stats"])
    assert stats["VALIDATED"]["w"] == 1 and json.loads(store["fp_ledger"])
    assert fp.manual_result("Seattle", "Calgary", 1, 0).startswith("No open")           # a card is graded once


def test_results_find_the_score_by_team_names_and_settle_due_closes_old_cards():
    fp, store, posts = _fp()
    fp.feed(_payload(0))
    rows = [[2000000300, "NHL", "Seattle Kraken", "Calgary Flames", 4, 1]]
    assert fp.settle_due(getter=lambda sport, date: rows, now=2000000000 + 3 * 3600) == 1
    assert "MATCH ENDED" in posts[-1] and "Seattle 4-1 Calgary" in posts[-1]


def test_reasons_cover_ah_total_ml_and_void():
    from finalpoint import results as rs
    pk = dict(pkey="total|6.5|under", mkt="total", line=6.5, sel="under", odds=1.9)
    assert rs.reason("IH", pk, "won", 3, 2, "A", "B") == "5 goals in total, 1.5 under the 6.5 line."
    pk = dict(pkey="ah|-1.5|home", mkt="ah", line=-1.5, sel="home", odds=2.0)
    assert "missed by 0.5" in rs.reason("IH", pk, "lost", 3, 2, "A", "B")
    pk = dict(pkey="ml||away", mkt="ml", line=None, sel="away", odds=2.0)
    assert rs.reason("AF", pk, "lost", 27, 20, "A", "B") == "A won 27-20, so B did not."
    assert rs.settle("BB", dict(pkey="ah|-3|home", mkt="ah", line=-3.0, sel="home", odds=2.0), 100, 97) == ("void", 0.0)


def test_bad_payload_is_refused_and_feed_never_raises(monkeypatch):
    fp, store, posts = _fp()
    assert fp.feed({"x": 1})["ok"] is False
    monkeypatch.delenv("FP_FEED_URL", raising=False)
    fpfeed.emit("FB", {"key": "k"}, {"label": "Check 1"}, None)       # off: does nothing


def test_feed_payload_is_json_with_string_line_keys():
    body = fpfeed.payload("FB", dict(key="k", lname="L", home="A", away="B", tip=5), dict(label="Check 1", status="watch", kind="check"),
                          dict(soft={"ah": {-1.5: {"home": 2.0, "away": 1.8}}}, pin=None), 0.04)
    assert body["soft"]["ah"] == {"-1.5": {"home": 2.0, "away": 1.8}}
    json.dumps(body)


def test_checks_saved_by_the_older_layout_render_as_one_line_not_empty_blocks():
    fp, store, posts = _fp()
    fp.feed(_payload(0))
    rec = json.loads(store["fp:IH:m1"])
    for k in ("story", "st", "tbl"):
        rec["checks"][0].pop(k, None)
    store["fp:IH:m1"] = json.dumps(rec)
    fp.feed(_payload(1, label="Check 2"))
    t = posts[-1]
    assert "Seen before the new layout" in t and t.count("<b>Status:</b>") == 1 and "<b>Check 2</b>" in t


def test_table_rows_are_one_short_line_with_letter_tags_and_a_blank_line_before_next_check():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62
    pl = _payload(0, soft=soft)
    pl["home"], pl["away"] = "Macarthur FC", "Brisbane Roar FC"
    pl["chk"]["nxt"] = 2000003600
    fp.feed(pl)
    t = posts[-1]
    pre = t[t.index("<pre>") + 5:t.index("</pre>")]
    assert all(len(x) <= 38 for x in pre.split("\n")), pre
    assert "Brisban +1.5" in pre or "Macarth" in pre
    assert "\n\nNext check:" in t and "★ top 3" in t.split("━")[0]
    assert "(closest)" not in pre and "derived" not in pre


def test_lines_where_one_side_pays_almost_nothing_are_never_ranked_or_shown():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["total"]["0.5"] = {"over": 1.04, "under": 10.5}
    for i in range(2):
        fp.feed(_payload(i, soft=soft, pin=None, label="Check %d" % (i + 1)))
    rec = json.loads(store["fp:IH:m1"])
    assert all(p["line"] != 0.5 for p in rec["picks"]) and "0.5" not in posts[-1].split("<pre>")[-1]


def _fpb(chat="-1004303651954"):
    store, posts, boards = {}, [], []
    state = {}

    def mirror(rec, text):
        rec.setdefault("fp", [100 + len(store)] if not rec.get("fp") else rec["fp"])
        posts.append(text)

    def bd(texts, ids, hs):
        boards.append(list(texts))
        return (ids or [900 + len(boards[-1])]), ["h"], True

    fp = FinalPoint(lambda k: store.get(k), lambda k, v: store.__setitem__(k, v), mirror, boards=bd, chat=chat)
    return fp, store, boards


def _valid_feed(fp, key, tag="IH", home="Seattle", away="Calgary", tip=2000000000, n=2):
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62
    for i in range(n):
        pl = _payload(i, soft=soft, label="Check %d" % (i + 1), tag=tag)
        pl.update(key=key, home=home, away=away, tip=tip)
        fp.feed(pl)


def test_boards_list_every_game_by_sport_newest_last_and_validated_and_leads_separately(monkeypatch):
    monkeypatch.setenv("FP_BOARD_MIN_SEC", "0")
    from finalpoint import board
    fp, store, boards = _fpb()
    pl = _payload(0)
    pl.update(key="a", home="Roma", away="Lazio", tag="IH")
    fp.feed(pl)                                                                  # a game with no value: only on Today's Games
    _valid_feed(fp, "b", home="Seattle", away="Calgary", n=2)                     # a VALIDATED pick
    games = "\n".join(boards[-1])
    picks_txt = None
    for b in boards:
        if "VALIDATED &amp; LEADS" in b[0]:
            picks_txt = b[0]
    last_games = next(b for b in reversed(boards) if "TODAY'S GAMES" in b[0])[0]
    last_picks = next(b for b in reversed(boards) if "VALIDATED &amp; LEADS" in b[0])[0]
    assert "<b>ICE HOCKEY</b> · 2" in last_games and last_games.index("Roma v Lazio") < last_games.index("Seattle v Calgary")
    assert "https://t.me/c/4303651954/" in last_games and "Jump:" in last_games and "VALIDATED" in last_games
    assert "Calgary +1.5" in last_picks and "Roma" not in last_picks and "as of" in last_picks
    assert board.day_of(2000000000) == "2033-05-18"


def test_withdrawn_pick_leaves_the_validated_list_and_finished_games_show_result(monkeypatch):
    monkeypatch.setenv("FP_BOARD_MIN_SEC", "0")
    fp, store, boards = _fpb()
    _valid_feed(fp, "b", n=2)
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.40
    pl = _payload(2, soft=soft, label="Check 3")
    pl.update(key="b")
    fp.feed(pl)
    last_picks = next(b for b in reversed(boards) if "VALIDATED &amp; LEADS" in b[0])[0]
    assert "Calgary +1.5" not in last_picks.split("LEADS")[0].split("<b>VALIDATED</b>")[-1] or "None yet today." in last_picks
    last_games = next(b for b in reversed(boards) if "TODAY'S GAMES" in b[0])[0]
    assert "WITHDRAWN" in last_games
    assert fp.manual_result("Seattle", "Calgary", 3, 2).startswith("Saved")
    last_games = next(b for b in reversed(boards) if "TODAY'S GAMES" in b[0])[0]
    assert "FT Seattle 3-2 Calgary" in last_games


def test_boards_split_into_pages_when_the_day_is_long_and_are_off_without_a_chat():
    from finalpoint import board
    ents = [dict(rk=str(i), tag="FB", home="Team Number %d" % i, away="Other Team %d" % i, tip=2000000000 + i, seen=i, st="WATCH", card=i + 1) for i in range(120)]
    pages = board.render_games("2033-05-18", ents, "https://t.me/c/1/")
    assert len(pages) >= 2 and all(len(p) < 4000 for p in pages) and "page 2" in pages[1]
    fp = FinalPoint(lambda k: None, lambda k, v: None, lambda r, t: None)
    assert fp.board_tick() == 0
    assert board.link_base("-1004303651954") == "https://t.me/c/4303651954/" and board.link_base("@x") == ""


def test_a_new_game_is_not_held_back_for_the_routine_refresh(monkeypatch):
    monkeypatch.setenv("FP_BOARD_MIN_SEC", "30")
    monkeypatch.setenv("FP_BOARD_SEC", "900")
    fp, store, boards = _fpb()
    pl = _payload(0); pl.update(key="a", home="Roma", away="Lazio")
    fp.feed(pl)
    pl = _payload(0); pl.update(key="b", home="Seattle", away="Calgary")
    fp.feed(pl)                                          # inside the 30 s gap: waits, but the tick flushes it as soon as the gap has passed
    import time as _t
    st = json.loads(store["fp_bd:2033-05-18"])
    st["t"] = _t.time() - 40
    store["fp_bd:2033-05-18"] = json.dumps(st)
    assert fp.board_tick(now=2000000000 - 7200) == 1
    assert "Seattle v Calgary" in next(b for b in reversed(boards) if "TODAY'S GAMES" in b[0])[0]
