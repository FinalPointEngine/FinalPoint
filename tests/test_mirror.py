from core import telegram_notify as tn

SEP = tn.SEP


def _setup(monkeypatch, mirror, calls, fail=False):
    class S:
        TELEGRAM_TOKEN = "SECRET"
    monkeypatch.setattr(tn, "settings", S)
    if mirror:
        monkeypatch.setenv("MIRROR_CHAT_ID", mirror)
    else:
        monkeypatch.delenv("MIRROR_CHAT_ID", raising=False)
    n = {"i": 100}

    def fake(url, payload, *a, **k):
        if fail:
            raise RuntimeError("chat not found SECRET")
        calls.append((url.rsplit("/", 1)[-1], dict(payload)))
        n["i"] += 1
        return {"result": {"message_id": n["i"]}}
    monkeypatch.setattr(tn, "post_json", fake)


def _card(n_checks, size=300):
    blocks = ["MATCH TRACKER\nA vs B"] + ["Check %d\n%s" % (i + 1, "x" * size) for i in range(n_checks)]
    return ("\n" + SEP + "\n").join(blocks)


M = lambda: {"home": "A", "away": "B"}


def test_nothing_when_unset(monkeypatch):
    calls = []
    _setup(monkeypatch, "", calls)
    tn.mirror_card(M(), _card(2))
    assert calls == []


def test_short_card_is_one_message_then_edited_in_place(monkeypatch):
    calls, m = [], M()
    _setup(monkeypatch, "-100999", calls)
    tn.mirror_card(m, _card(2))
    assert [c[0] for c in calls] == ["sendMessage"] and calls[0][1]["chat_id"] == "-100999" and "reply_markup" not in calls[0][1]
    tn.mirror_card(m, _card(3))
    assert calls[-1][0] == "editMessageText" and calls[-1][1]["message_id"] == m["fp"][0]
    n = len(calls)
    tn.mirror_card(m, _card(3))
    assert len(calls) == n                      # unchanged card: nothing sent


def test_long_card_continues_on_a_new_page_linked_to_the_previous(monkeypatch):
    calls, m = [], M()
    _setup(monkeypatch, "-100999", calls)
    tn.mirror_card(m, _card(8, size=800))
    sends = [c[1] for c in calls if c[0] == "sendMessage"]
    assert len(sends) >= 2
    assert all(len(s["text"]) <= 4000 for s in sends)
    assert "reply_to_message_id" not in sends[0] and sends[1]["reply_to_message_id"] == m["fp"][0]
    assert "page 2" in sends[1]["text"] and "Check 1" in sends[0]["text"]
    assert sum(s["text"].count("\nCheck ") + s["text"].startswith("Check ") for s in sends) >= 8


def test_failure_is_harmless(monkeypatch):
    calls = []
    _setup(monkeypatch, "-100999", calls, fail=True)
    tn.mirror_card(M(), _card(2))              # must not raise


def test_plain_api_never_mirrors(monkeypatch):
    calls = []
    _setup(monkeypatch, "-100999", calls)
    tn.api("sendMessage", chat_id="1", text="error or summary")
    assert [c[1]["chat_id"] for c in calls] == ["1"]
