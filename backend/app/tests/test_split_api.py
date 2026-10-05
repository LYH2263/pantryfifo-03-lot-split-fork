import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import connect


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(main.app) as c:
        yield c


def q(sql, args=()):
    c = connect()
    rows = [dict(r) for r in c.execute(sql, args)]
    c.close()
    return rows


def lot_rows():
    return q("SELECT * FROM lots ORDER BY id")


def make_item(layer="upper", unit="盒"):
    c = connect()
    cur = c.execute("INSERT INTO items(name,layer,unit) VALUES (?,?,?)", ("测试品", layer, unit))
    c.commit(); iid = cur.lastrowid; c.close()
    return iid


def inb(client, item_id, qty, expiry="2026-12-15"):
    r = client.post("/api/lots", json={"item_id": item_id, "qty": qty, "expiry": expiry})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def fridge_total(client, item_id, layer=None):
    path = f"/api/fridge?layer={layer}" if layer else "/api/fridge"
    rows = client.get(path).json()
    return round(sum(r["qty_remain"] for r in rows if r["item_id"] == item_id), 6)


# --- quantity conservation -------------------------------------------------

def test_split_conserves_and_marks_parent(client):
    iid = make_item()
    lid = inb(client, iid, 10)
    r = client.post("/api/split", json={"lot_id": lid, "qty": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"]
    assert body["parent_id"] == lid
    assert body["child_qty"] == 3 and body["parent_remain"] == 7
    assert body["total"] == 10  # child + mother == before split
    rows = {r["id"]: r for r in lot_rows()}
    child = rows[body["child_id"]]
    assert child["parent_id"] == lid and child["qty_remain"] == 3
    assert rows[lid]["qty_remain"] == 7
    # child shelved iff mother reduced — both happened, statuses consistent
    assert child["status"] == "on_shelf" and rows[lid]["status"] == "on_shelf"


def test_fridge_and_layer_pages_sum_the_same(client):
    iid = make_item(layer="mid")
    lid = inb(client, iid, 10)
    before_all = fridge_total(client, iid)
    before_mid = fridge_total(client, iid, "mid")
    assert before_all == before_mid == 10
    r = client.post("/api/split", json={"lot_id": lid, "qty": 4})
    assert r.status_code == 200
    # both pages total to the same unchanged sum after the split
    assert fridge_total(client, iid) == 10
    assert fridge_total(client, iid, "mid") == 10
    assert fridge_total(client, iid, "upper") == 0


# --- failure orders leave no trace ----------------------------------------

def test_split_zero_fails_entire_order(client):
    iid = make_item()
    lid = inb(client, iid, 10)
    before = lot_rows()
    r = client.post("/api/split", json={"lot_id": lid, "qty": 0})
    assert r.status_code == 400
    assert lot_rows() == before  # back to pre-split state


def test_split_full_and_over_remain_fails_without_orphan(client):
    iid = make_item()
    lid = inb(client, iid, 10)
    for bad in (10, 10.0001):
        r = client.post("/api/split", json={"lot_id": lid, "qty": bad})
        assert r.status_code == 409, (bad, r.text)
    rows = lot_rows()
    # mother untouched, no child/orphan row attached to it
    mother = [r for r in rows if r["id"] == lid][0]
    assert mother["qty_remain"] == 10
    assert [r for r in rows if r.get("parent_id") == lid] == []


def test_dirty_and_negative_lots_cannot_split(client):
    # seeded: lot 4 dirty qty 1 (item 3), lot 5 dirty qty -3 (item 2)
    for lid in (4, 5, 9999):
        r = client.post("/api/split", json={"lot_id": lid, "qty": 1})
        assert r.status_code in (404, 409)
    assert [r for r in lot_rows() if r.get("parent_id")] == []


# --- FEFO after split must land on the child row ---------------------------

def test_consume_after_split_hits_child_not_vanished_mother(client):
    iid = make_item()
    lid = inb(client, iid, 10, expiry="2026-10-20")
    sp = client.post("/api/split", json={"lot_id": lid, "qty": 3}).json()
    child = sp["child_id"]
    r = client.post("/api/consume", json={"item_id": iid, "qty": 4})
    assert r.status_code == 200, r.text
    deductions = r.json()["deductions"]
    assert deductions[0]["lot_id"] == child  # repacked row consumed first
    rows = {x["id"]: x for x in lot_rows()}
    assert rows[child]["status"] == "consumed" and rows[child]["qty_remain"] == 0
    assert rows[lid]["qty_remain"] == 6  # 10 - 3 split - 1 spillover consume
    # shelf total still conserved: 10 - 4 consumed
    assert fridge_total(client, iid) == 6


def test_earlier_expiry_still_outranks_child(client):
    iid = make_item()
    lid = inb(client, iid, 10, expiry="2026-12-20")
    client.post("/api/split", json={"lot_id": lid, "qty": 3})
    other = inb(client, iid, 2, expiry="2026-11-01")
    r = client.post("/api/consume", json={"item_id": iid, "qty": 2}).json()
    assert [d["lot_id"] for d in r["deductions"]] == [other]


# --- split + consume + expiry stacked on one mother ------------------------

def test_split_then_expire_sweep_takes_both_rows(client):
    iid = make_item()
    lid = inb(client, iid, 2, expiry="2026-10-01")  # already past today
    sp = client.post("/api/split", json={"lot_id": lid, "qty": 1}).json()
    child = sp["child_id"]
    sweep = client.post("/api/expire-sweep").json()
    assert set(sweep["expired_ids"]) >= {lid, child}
    rows = {x["id"]: x for x in lot_rows()}
    assert rows[lid]["status"] == "expired" and rows[child]["status"] == "expired"


def test_consume_split_sweep_stacked_conservation(client):
    iid = make_item()
    lid = inb(client, iid, 10, expiry="2026-10-02")  # past today
    sp = client.post("/api/split", json={"lot_id": lid, "qty": 4}).json()
    child = sp["child_id"]
    # consume the child row fully first
    r = client.post("/api/consume", json={"item_id": iid, "qty": 4})
    assert r.status_code == 200
    assert r.json()["deductions"][0]["lot_id"] == child
    # sweep then removes only the leftover mother, not a phantom child
    swept = set(client.post("/api/expire-sweep").json()["expired_ids"])
    assert lid in swept and child not in swept
    rows = {x["id"]: x for x in lot_rows()}
    assert rows[child]["status"] == "consumed"
    assert rows[lid]["status"] == "expired" and rows[lid]["qty_remain"] == 6


# --- consumption history recorded against mother must not re-deduct child --

def test_prior_consumption_history_is_not_replayed_onto_child(client):
    iid = make_item()
    lid = inb(client, iid, 10, expiry="2026-10-20")
    first = client.post("/api/consume", json={"item_id": iid, "qty": 2}).json()
    assert [d["lot_id"] for d in first["deductions"]] == [lid]
    sp = client.post("/api/split", json={"lot_id": lid, "qty": 3}).json()
    child = sp["child_id"]
    rows = {x["id"]: x for x in lot_rows()}
    # child starts at exactly its split qty; the historical mother
    # deduction (recorded in consumptions) is not applied to it again
    assert rows[child]["qty_remain"] == 3
    assert rows[lid]["qty_remain"] == 5
    hist = [json.loads(r["result_json"]) for r in q("SELECT * FROM consumptions")]
    assert hist[0]["deductions"][0]["lot_id"] == lid  # history keeps mother id
    # and the child is independently usable from its own 3 units
    again = client.post("/api/consume", json={"item_id": iid, "qty": 3}).json()
    assert again["deductions"][0]["lot_id"] == child
    assert {x["id"]: x for x in lot_rows()}[child]["qty_remain"] == 0
