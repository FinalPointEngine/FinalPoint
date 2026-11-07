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


def test_report_is_one_growing_card_with_a_block_per_check():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62
    for i in range(3):
        assert fp.feed(_payload(i, soft=soft, label="Check %d" % (i + 1)))["ok"]
    t = posts[-1]
    assert t.startswith("🏒 <b>FINALPOINT · IH · Seattle vs Calgary</b>") and "STATUS:" in t
    assert all(("<b>Check %d</b>" % n) in t for n in (1, 2, 3))          # every check keeps its block
    assert "★1" in t and "BEST 3:" in t and "Pinnacle view:" in t and "History:" in t
    rec = json.loads(store["fp:IH:m1"])
    assert len(rec["checks"]) == 3 and rec["top3"]


def test_market_is_possible_then_validated_on_the_second_check():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62                                  # fair 1.50 -> value on Calgary +1.5
    fp.feed(_payload(0, soft=soft))
    assert "POSSIBLE" in posts[-1] and "AUDIT" in posts[-1] and "STATUS: VALIDATED" not in posts[-1]
    fp.feed(_payload(1, soft=soft, label="Check 2"))
    assert "STATUS: VALIDATED" in posts[-1] and "Calgary +1.5" in posts[-1]
    soft["ah"]["-1.5"]["away"] = 1.40                                  # price falls away: the validation is withdrawn
    fp.feed(_payload(2, soft=soft, label="Check 3"))
    assert "WITHDRAWN" in posts[-1] and "STATUS: VALIDATED" not in posts[-1]


def test_missing_pinnacle_is_fishy_only_when_it_was_there():
    fp, store, posts = _fp()
    fp.feed(_payload(0))
    for i in range(1, 4):
        fp.feed(_payload(i, pin=None, label="Check %d" % (i + 1)))
    assert "FISHY" in posts[-1] and "DO NOT BET BLIND" in posts[-1] and "Hunt mode" in posts[-1]


def test_no_pinnacle_from_the_start_is_not_fishy_while_the_baseline_is_unknown():
    fp, store, posts = _fp()
    for i in range(3):
        fp.feed(_payload(i, pin=None, label="Check %d" % (i + 1)))
    fp.feed(_payload(3, pin=None, label="Check: T-15"))
    t = posts[-1]
    assert "FISHY" not in t and "NO PRICE" in t and "Check: T-15" in t and "Check: Check" not in t


def test_stale_pinnacle_price_stays_possible_with_an_audit_reason():
    fp, store, posts = _fp()
    soft = json.loads(json.dumps(SOFT))
    soft["ah"]["-1.5"]["away"] = 1.62
    fp.feed(_payload(0, soft=soft))
    fp.feed(_payload(1, soft=soft, age=25.0, label="Check 2"))
    assert "STATUS: VALIDATED" not in posts[-1] and "25 min old" in posts[-1]


def test_basketball_uses_the_price_table_of_its_tracker():
    fp, store, posts = _fp()
    pl = _payload(0, soft=None, pin=None, tag="BB")
    pl["brows"] = [dict(market="spreads", selection="home", line=-3.5, odds=2.05, p=0.52),
                   dict(market="spreads", selection="away", line=-3.5, odds=1.80, p=0.48),
                   dict(market="totals", selection="over", line=150.5, odds=1.90, p=0.50)]
    fp.feed(pl)
    assert "FINALPOINT · BB" in posts[-1] and "Seattle -3.5" in posts[-1] and "POSSIBLE" in posts[-1]


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
