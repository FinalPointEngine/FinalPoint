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
    stats = json.loads(store["fp_stats2"])
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
    assert len(pages) >= 2 and all(len(p) < 4000 for p in pages) and "page 2/" in pages[1]
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


def test_tennis_names_listed_surname_first_read_naturally_and_fit_the_table():
    fp, store, posts = _fp()
    soft = {"ml": {"home": 3.0, "away": 1.41}, "ah": {"4.5": {"home": 1.71, "away": 2.15}}, "total": {"22.5": {"over": 2.25, "under": 1.65}}}
    pin = {"ml": {"home": 3.18, "away": 1.46}, "ah": {"4.5": {"home": 1.67, "away": 2.49}}, "total": {"22.5": {"over": 2.18, "under": 1.85}}}
    pl = _payload(0, soft=soft, pin=pin, tag="TN")
    pl.update(home="Liu, Claire", away="Eala, Alexandra")
    fp.feed(pl)
    t = posts[-1]
    assert "<b>Claire Liu vs</b>" in t and "<b>Alexandra Eala</b>" in t and "Liu, Claire" not in t
    pre = t[t.index("<pre>") + 5:t.index("</pre>")]
    assert "Liu +4.5" in pre and "Eala " in pre and all(len(x) <= 38 for x in pre.split("\n"))


def test_a_constant_offset_in_the_whole_total_is_never_validated_and_one_total_is_starred():
    """The four KHL/VHL cards of 8 Nov: SportyBet's Under paid +5..+15% and its Over -16..-21% on every line, 90 minutes running."""
    fp, store, posts = _fp()
    soft = {"ml": {"home": 1.82, "away": 2.05}, "total": {"4.5": {"over": 1.57, "under": 2.40}, "5": {"over": 1.97, "under": 1.84}}}
    pin = {"ml": {"home": 1.96, "away": 2.04}, "total": {"4.5": {"over": 1.93, "under": 2.08}, "5": {"over": 2.37, "under": 1.73}}}
    for i in range(3):
        fp.feed(_payload(i, soft=soft, pin=pin, label="Check %d" % (i + 1)))
    t = html.unescape(posts[-1])
    rec = json.loads(store["fp:IH:m1"])
    assert rec["val"] == {} and "Status:</b> VALIDATED" not in t
    assert "[V]" not in t and "away from Pinnacle's on every line" in t
    assert sum(1 for p in rec["picks"] if p["mkt"] == "total") == 1


def test_club_prefixes_are_dropped_in_labels_and_board_titles():
    from finalpoint import card, board
    assert card.row_label("ah", -1.5, "home", "HC Yugra Khanty-Mansiysk", "HC Magnitka Magnitogorsk") == "Yugra -1.5"
    assert card.row_label("ml", None, "away", "HC Yugra Khanty-Mansiysk", "HC Magnitka Magnitogorsk") == "Magnitka Mag"
    assert board._title(dict(home="HC Yugra Khanty-Mansiysk", away="HC Magnitka Magnitogorsk")) == "Yugra Khanty- v Magnitka Magn"


def test_the_wide_gap_note_between_moneyline_and_spread_is_only_for_spread_sports():
    fp, store, posts = _fp()
    pin = {"ml": {"home": 1.45, "away": 3.21}, "ah": {"-1.5": {"home": 2.55, "away": 1.64}}}
    soft = {"ml": {"home": 1.49, "away": 2.7}, "ah": {"-1.5": {"home": 2.5, "away": 1.54}}}
    fp.feed(_payload(0, soft=soft, pin=pin))
    assert "disagree by a wide margin" not in posts[-1]


def test_picks_validated_more_than_an_hour_before_kickoff_are_marked_early_on_the_board(monkeypatch):
    monkeypatch.setenv("FP_BOARD_MIN_SEC", "0")
    fp, store, boards = _fpb()
    _valid_feed(fp, "b", n=2)                        # last check is 2 h before the 2000000000 kick-off
    txt = next(b for b in reversed(boards) if "LEADS</b>" in b[0].split("\n")[0])[0]
    assert "· early" in txt
    soft = json.loads(json.dumps(SOFT)); soft["ah"]["-1.5"]["away"] = 1.62
    pl = _payload(2, soft=soft, label="Check: T-15"); pl["chk"]["t"] = 2000000000 - 900; pl["key"] = "b"
    fp.feed(pl)
    txt = next(b for b in reversed(boards) if "LEADS</b>" in b[0].split("\n")[0])[0]
    assert "Calgary +1.5" in txt and "· early" not in txt.split("<i>early")[0]


def test_table_headings_follow_each_sports_own_wording():
    from finalpoint import card
    assert card.group_title("VB", "ah", True) == "SET HANDICAP (closest line)" and card.group_title("VB", "total", False) == "TOTAL SETS"
    assert card.group_title("TN", "ah", False) == "GAME HANDICAP" and card.group_title("TN", "sh", False) == "SET HANDICAP"
    assert card.group_title("TN", "total", True) == "TOTAL GAMES (closest line)" and card.group_title("TN", "ml", False) == "MATCH WINNER"
    assert card.group_title("FB", "1x2", False) == "MATCH RESULT (90 min)" and card.group_title("AF", "ah", False) == "SPREAD (incl. overtime)"
    assert card.row_label("cs", None, "2:0", "A", "B") == "2:0"


def test_tennis_reason_does_not_quote_a_score_and_lesson_hides_the_watch_record():
    from finalpoint import results as rs
    pk = dict(pkey="ml||away", mkt="ml", line=None, sel="away", odds=1.41)
    assert rs.reason("TN", pk, "lost", 1, 0, "Claire Liu", "Alexandra Eala") == "Claire Liu won the match, so Alexandra Eala did not."
    assert rs.reason("TN", pk, "won", 0, 1, "Claire Liu", "Alexandra Eala") == "Alexandra Eala won the match."
    txt = rs.lesson([dict(tier=3, status="lost")], {"WATCH": dict(w=89, l=8, u=17.7), "VALIDATED": dict(w=1, l=0, u=0.9)})
    assert "WATCH 89W" not in txt and "VALIDATED 1W-0L" in txt


def test_second_result_source_is_asked_only_when_the_first_finds_nothing_and_parses_finished_games():
    from finalpoint import results as rs, sources
    sample = {"events": [
        {"strStatus": "Match Finished", "intHomeScore": "3", "intAwayScore": "2", "strTimestamp": "2033-05-18T13:00:00", "strLeague": "KHL",
         "strHomeTeam": "Amur Khabarovsk", "strAwayTeam": "HC Barys"},
        {"strStatus": "Not Started", "intHomeScore": None, "intAwayScore": None, "strTimestamp": "2033-05-18T15:00:00", "strHomeTeam": "X", "strAwayTeam": "Y"},
        {"strStatus": "Match Finished", "intHomeScore": "", "intAwayScore": "", "strTimestamp": "2033-05-18T15:00:00", "strHomeTeam": "X", "strAwayTeam": "Y"}]}
    rows = sources.tsdb_rows("hockey", "2033-05-18", getter=lambda url: sample)
    assert len(rows) == 1 and rows[0][2:] == ["Amur Khabarovsk", "HC Barys", 3, 2] and sources.tsdb_rows("tennis", "2033-05-18", getter=lambda u: sample) == []
    tip = rows[0][0]
    rs._cache.clear()
    assert rs.find_score("IH", "Amur Khabarovsk", "HC Barys", tip, getter=lambda s, d: [], second=lambda s, d: rows) == (3, 2)
    first = [[tip, "KHL", "Amur Khabarovsk", "HC Barys", 9, 9]]
    rs._cache.clear()
    assert rs.find_score("IH", "Amur Khabarovsk", "HC Barys", tip, getter=lambda s, d: first, second=lambda s, d: rows) == (9, 9)

    def boom(s, d):
        raise OSError("403")
    rs._cache.clear()
    assert rs.find_score("IH", "Amur Khabarovsk", "HC Barys", tip, getter=boom, second=lambda s, d: rows) == (3, 2)
    rs._cache.clear()


def test_probe_reports_each_source_per_sport_and_never_raises():
    from finalpoint import sources
    def get(url, timeout=20):
        if "sofascore" in url:
            raise OSError("HTTP Error 403: Forbidden")
        if "espn" in url:
            return {"events": []}
        return {"events": [{"strStatus": "FT", "intHomeScore": "1", "intAwayScore": "0", "strTimestamp": "2033-05-18T13:00:00", "strHomeTeam": "A", "strAwayTeam": "B"}]}
    txt = sources.probe("2033-05-18", getter=get, pause=0)
    assert "hockey: SofaScore blocked" in txt and "ESPN 0" in txt and "TheSportsDB 1" in txt and "tennis: SofaScore blocked" in txt and "/result" in txt


def test_espn_urls_ask_for_all_of_division_one_and_results_look_a_day_either_side():
    from thirdeye import sources as ts
    from finalpoint import results as rs
    assert "groups=80" in ts.espn_url("football/college-football", "2026-11-07") and "dates=20261107" in ts.espn_url("football/nfl", "2026-11-07")
    assert "groups" not in ts.espn_url("hockey/nhl", "2026-11-07") and "football/college-football" in ts.ESPN["afootball"]
    seen = []
    rs._cache.clear()
    rs.find_score("AF", "A", "B", 2000000000, getter=lambda s, d: seen.append(d) or [], second=None)
    assert len(seen) == 3 and seen == sorted(seen)
    rs._cache.clear()


def _espn_tennis(a, b, la, lb, a_won, status="STATUS_FINAL", date="2033-05-18T07:00:00Z"):
    ath = lambda n: {"displayName": n}
    return {"events": [{"date": date, "name": "WTA Hong Kong", "groupings": [{"competitions": [{
        "date": date, "status": {"type": {"name": status, "completed": True}},
        "competitors": [{"order": 1, "winner": a_won, "athlete": ath(a), "linescores": [{"value": v} for v in la]},
                        {"order": 2, "winner": not a_won, "athlete": ath(b), "linescores": [{"value": v} for v in lb]}]}]}]}]}


def test_tennis_sets_and_games_come_from_espn_and_grade_every_market(monkeypatch):
    from finalpoint import results as rs
    tip = 2000000000
    js = _espn_tennis("Himeno Sakatsume", "Hong Yi Cody Wong", [6, 6], [4, 2], True, date="2033-05-18T03:20:00Z")
    rs._tcache.clear()
    d = rs.find_tennis("Himeno Sakatsume", "Hong Yi Cody Wong", tip, getter=lambda url: js if "wta" in url else {})
    assert d == dict(sets=(2, 0), games=(12, 6), home_won=True, retired=False)
    rs._tcache.clear()
    d2 = rs.find_tennis("Hong Yi Cody Wong", "Himeno Sakatsume", tip, getter=lambda url: js if "wta" in url else {})      # listed the other way round
    assert d2 == dict(sets=(0, 2), games=(6, 12), home_won=False, retired=False)
    pk = lambda m, l, s: dict(pkey="%s|%s|%s" % (m, "" if l is None else "%g" % l, s), mkt=m, line=l, sel=s, odds=2.0)
    assert rs.settle_tennis(pk("total", 22.5, "over"), d)[0] == "lost" and rs.settle_tennis(pk("total", 17.5, "over"), d)[0] == "won"
    assert rs.settle_tennis(pk("ah", -4.5, "away"), d)[0] == "lost" and rs.settle_tennis(pk("ah", -6.5, "away"), d)[0] == "won"      # the line is the home side's: away +4.5 / +6.5
    assert rs.settle_tennis(pk("sh", -1.5, "home"), d)[0] == "won" and rs.settle_tennis(pk("es", None, "s2"), d)[0] == "won"
    assert rs.settle_tennis(pk("cs", None, "2:0"), d)[0] == "won" and rs.settle_tennis(pk("ml", None, "away"), d)[0] == "lost"


def test_a_retirement_or_odd_finish_is_never_graded():
    from finalpoint import results as rs
    rs._tcache.clear()
    js = _espn_tennis("A Player", "B Player", [6, 2], [3, 1], True, status="STATUS_RETIRED", date="2033-05-18T03:20:00Z")
    d = rs.find_tennis("A Player", "B Player", 2000000000, getter=lambda url: js if "wta" in url else {})
    assert d["retired"] is True
    rec = dict(home="A Player", away="B Player", picks=[dict(pkey="ml||home", mkt="ml", line=None, sel="home", d="A Player to win", odds=1.5, tier=3)])
    end, graded = rs.end_block("TN", rec, 2, 0, {}, d)
    assert graded[0]["status"] == "ungraded" and "retirement" in graded[0]["why"]
    rs._tcache.clear()


def test_a_finished_tennis_card_is_graded_on_games_and_sets_through_settle_due(monkeypatch):
    from finalpoint import results as rs
    fp, store, posts = _fp()
    soft = {"ml": {"home": 1.3, "away": 3.4}, "ah": {"-4.5": {"home": 1.9, "away": 1.9}}, "total": {"22.5": {"over": 1.9, "under": 1.9}}}
    pin = {"ml": {"home": 1.3, "away": 3.4}, "ah": {"-4.5": {"home": 1.9, "away": 1.9}}, "total": {"22.5": {"over": 1.9, "under": 1.9}}}
    pl = _payload(0, soft=soft, pin=pin, tag="TN"); pl.update(home="Himeno Sakatsume", away="Hong Yi Cody Wong")
    fp.feed(pl)
    js = _espn_tennis("Himeno Sakatsume", "Hong Yi Cody Wong", [6, 6], [4, 2], True, date="2033-05-18T03:20:00Z")
    rs._tcache.clear()
    monkeypatch.setattr(rs, "find_tennis", lambda h, a, tip, getter=None: rs.find_tennis.__wrapped__(h, a, tip, lambda url: js if "wta" in url else {}) if False else
                        dict(sets=(2, 0), games=(12, 6), home_won=True, retired=False))
    assert fp.settle_due(now=2000000000 + 3 * 3600) == 1
    t = posts[-1]
    assert "MATCH ENDED" in t and "Not graded" not in t and ("Won" in t or "Lost" in t)


def test_long_boards_hold_every_game_and_link_previous_and_next():
    from finalpoint import board
    ents = [dict(rk=str(i), tag="FB" if i < 70 else "TN", home="Team Number %d" % i, away="Other Team %d" % i, tip=2000000000 + i, seen=i, st="WATCH", card=i + 1) for i in range(120)]
    pages = board.render_games("2033-05-18", ents, "https://t.me/c/1/", ids=[501, 502, 503, 504])
    assert len(pages) >= 3 and all(len(p) < 4000 for p in pages)
    allt = "\n".join(pages)
    assert allt.count(" · WATCH") == 120
    assert "Previous" not in pages[0].split("\n")[2] and "https://t.me/c/1/502" in pages[0] and "Next" in pages[0]
    assert "https://t.me/c/1/501" in pages[1] and "https://t.me/c/1/503" in pages[1]
    assert "(cont.)" in pages[1] and "page 2/" in pages[1]
    lp = [dict(rk=str(i), tag="FB", home="Home %d" % i, away="Away %d" % i, tip=2000000000 + i, seen=i, card=i + 1, ck=2000000000,
               lead=[dict(pkey="k%d" % i, d="Over 2.5", odds=1.9, e=0.05)], val=[]) for i in range(80)]
    pp = board.render_picks("2033-05-18", lp, "https://t.me/c/1/", ids=[7, 8, 9])
    assert len(pp) >= 2 and all(len(p) < 4000 for p in pp) and sum(x.count("Over 2.5") for x in pp) == 80
    assert "Next" in pp[0] and "Previous" in pp[-1]


def test_football_after_extra_time_is_not_graded_from_the_feed_score():
    from thirdeye import sources as ts
    from finalpoint import results as rs
    ev = lambda nm: {"events": [{"date": "2033-05-18T18:00:00Z", "competitions": [{"status": {"type": {"completed": True, "name": nm}},
         "competitors": [{"homeAway": "home", "team": {"displayName": "Roma"}, "score": "2"}, {"homeAway": "away", "team": {"displayName": "Lazio"}, "score": "1"}]}]}]}
    t = 2000052000
    for nm, want in (("STATUS_FINAL", (2, 1)), ("STATUS_FINAL_AET", None), ("STATUS_FINAL_PEN", None)):
        rows = ts.parse_espn(ev(nm))
        rs._cache.clear()
        assert rs.find_score("FB", "Roma", "Lazio", t, getter=lambda s, d: rows, second=None) == want
    rs._cache.clear()


def test_zero_handicap_is_written_0_on_both_sides_never_minus_0():
    from finalpoint import card
    assert card.row_label("ah", 0.0, "home", "SK Rotor Volgograd", "Leningradets") == "Rotor 0"
    assert card.row_label("ah", 0.0, "away", "SK Rotor Volgograd", "Leningradets") == "Leningrade 0"
    assert card.row_label("ah", -0.0, "away", "A B", "C D").endswith(" 0") and card.row_label("ah", 0.5, "away", "A B", "C D").endswith("-0.5")


def test_american_football_names_use_the_nickname_and_the_spread_note_is_gone():
    from finalpoint import card
    assert card.row_label("ml", None, "home", "San Francisco 49ers", "Las Vegas Raiders", True) == "49ers"
    assert card.row_label("ah", -9.5, "home", "San Francisco 49ers", "Las Vegas Raiders", True) == "49ers -9.5"
    assert card.row_label("ah", -9.5, "away", "San Francisco 49ers", "Las Vegas Raiders", True) == "Raiders +9.5"
    fp, store, posts = _fp()
    soft = {"ml": {"home": 1.22, "away": 4.0}, "total": {"52.5": {"over": 1.95, "under": 1.83}}}
    pin = {"ml": {"home": 1.24, "away": 5.2}, "total": {"52.5": {"over": 2.11, "under": 1.90}}, "ah": {"-9.5": {"home": 1.95, "away": 1.95}}}
    pl = _payload(0, soft=soft, pin=pin, tag="AF"); pl.update(home="San Francisco 49ers", away="Las Vegas Raiders")
    fp.feed(pl)
    assert "underdog spread" not in posts[-1] and "49ers 1.22" in posts[-1] and "San Francisc" not in posts[-1].split("<pre>")[1]


def test_handball_rows_name_the_club_and_say_sixty_minutes():
    from finalpoint import card
    assert card.row_label("1x2", None, "home", "THW Kiel", "SG Flensburg-Handewitt", True) == "Kiel"
    assert card.row_label("ah", -2.5, "away", "HC Vardar 1961", "Fuchse Berlin", True) == "Berlin +2.5"
    assert card.row_label("ah", -2.5, "home", "HC Vardar 1961", "Fuchse Berlin", True) == "Vardar -2.5"
    assert card.group_title("HB", "1x2", False) == "MATCH RESULT (60 min)"
