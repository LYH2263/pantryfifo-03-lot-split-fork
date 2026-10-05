from app.engines.split import plan_split

LOT = {"id": 1, "item_id": 7, "qty_remain": 10.0,
       "expiry": "2026-12-01", "status": "on_shelf", "data_quality": "clean",
       "parent_id": None}


def test_plan_conserves_quantity():
    p = plan_split(dict(LOT), 3)
    assert p["ok"]
    assert p["child_qty"] == 3
    assert p["mother_after"] == 7
    assert p["total_after"] == 10  # child + mother == before


def test_zero_and_negative_fail():
    assert plan_split(dict(LOT), 0)["reason"] == "split_qty_non_positive"
    assert plan_split(dict(LOT), -2)["reason"] == "split_qty_non_positive"


def test_split_more_than_remain_fails():
    assert plan_split(dict(LOT), 10)["reason"] == "split_qty_exceeds_remain"
    assert plan_split(dict(LOT), 12)["reason"] == "split_qty_exceeds_remain"


def test_missing_lot():
    assert plan_split(None, 1)["reason"] == "lot_not_found"


def test_dirty_lot_not_splittable():
    # Dirty lots are not consume candidates; they must not be splittable either.
    dirty = dict(LOT, data_quality="dirty")
    assert plan_split(dirty, 1)["reason"] == "lot_not_eligible"


def test_negative_remain_row_not_splittable():
    neg = dict(LOT, qty_remain=-3)
    assert plan_split(neg, 1)["reason"] == "lot_not_eligible"


def test_off_shelf_lot_not_splittable():
    gone = dict(LOT, status="consumed", qty_remain=0)
    assert plan_split(gone, 1)["reason"] == "lot_not_eligible"
