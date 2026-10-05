from app.engines.fefo import consume_fefo, expire_lots, sort_lots_fefo, is_eligible

CLEAN = {"status": "on_shelf", "data_quality": "clean"}


def test_fefo_order():
    lots = [
        {"id": 2, "qty_remain": 3, "expiry": "2026-02-01", **CLEAN},
        {"id": 1, "qty_remain": 2, "expiry": "2026-01-10", **CLEAN},
    ]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [1, 2]
    r = consume_fefo(lots, 3)
    assert r["ok"] and r["deductions"][0]["lot_id"] == 1 and r["deductions"][0]["take"] == 2
    assert r["deductions"][1]["take"] == 1

def test_short():
    r = consume_fefo([{"id": 1, "qty_remain": 1, "expiry": "2026-01-01", **CLEAN}], 5)
    assert r["ok"] is False and r["short"] == 4

def test_expire():
    ids = expire_lots([
        {"id": 1, "qty_remain": 1, "expiry": "2025-01-01"},
        {"id": 2, "qty_remain": 1, "expiry": "2027-01-01"},
    ], "2026-01-01")
    assert ids == [1]

def test_dirty_and_negative_are_not_candidates():
    lots = [
        {"id": 1, "qty_remain": 5, "expiry": "2026-01-01", "status": "on_shelf", "data_quality": "dirty"},
        {"id": 2, "qty_remain": -3, "expiry": "2026-01-02", "status": "on_shelf", "data_quality": "dirty"},
        {"id": 3, "qty_remain": 4, "expiry": "2026-02-01", **CLEAN},
    ]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [3]
    assert is_eligible(lots[0]) is False
    assert is_eligible(lots[1]) is False
    r = consume_fefo(lots, 5)
    assert r["ok"] is False and r["reason"] == "short"
    assert [d["lot_id"] for d in r["deductions"]] == [3]

def test_split_child_ranks_before_mother_same_expiry():
    # Same item/expiry after a repack: FEFO must land on the child row.
    lots = [
        {"id": 10, "qty_remain": 7, "expiry": "2026-11-01", "parent_id": None, **CLEAN},
        {"id": 11, "qty_remain": 3, "expiry": "2026-11-01", "parent_id": 10, **CLEAN},
    ]
    assert [l["id"] for l in sort_lots_fefo(lots)] == [11, 10]
    r = consume_fefo(lots, 4)
    assert r["ok"]
    assert [d["lot_id"] for d in r["deductions"]] == [11, 10]
    assert r["deductions"][0]["take"] == 3 and r["deductions"][1]["take"] == 1
