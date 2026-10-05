import json
from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.fefo import consume_fefo, expire_lots
from app.engines.split import plan_split

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# plan_split failure reason -> HTTP status code
_SPLIT_STATUS = {
    "lot_not_found": 404,
    "lot_not_eligible": 409,
    "split_qty_non_positive": 400,
    "split_qty_exceeds_remain": 409,
}


@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    c = connect()
    q = """SELECT lots.*, items.name, items.layer, items.unit FROM lots
           JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf'"""
    args = []
    if layer:
        q += " AND items.layer=?"; args.append(layer)
    q += " ORDER BY items.layer, lots.expiry, lots.id"
    rows = [dict(r) for r in c.execute(q, args)]; c.close(); return rows

@app.get("/api/alerts")
def alerts():
    c = connect()
    warn = int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])
    today = date.today().isoformat()
    rows = [dict(r) for r in c.execute(
        """SELECT lots.*, items.name, items.layer FROM lots JOIN items ON items.id=lots.item_id
           WHERE status='on_shelf' AND qty_remain>0 AND expiry IS NOT NULL""")]
    c.close()
    out = []
    for r in rows:
        if r["expiry"] <= today:
            r["level"] = "expired"
            out.append(r)
        else:
            # simple day diff via fromisoformat
            delta = (date.fromisoformat(r["expiry"]) - date.today()).days
            if delta <= warn:
                r["level"] = "soon"; r["days_left"] = delta; out.append(r)
    return out

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    c = connect()
    item = c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
    c.commit(); lid = cur.lastrowid; c.close(); return {"id": lid}

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    note: str = ""

@app.post("/api/consume")
def consume(body: ConsumeIn):
    c = connect()
    try:
        # Pull every shelf row and let the engine apply the shared eligibility
        # gate (positive remainder + clean), so dirty/negative rows can never
        # silently enter FEFO picking here or anywhere else.
        lots = [dict(r) for r in c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf'", (body.item_id,))]
        result = consume_fefo(lots, body.qty)
        if not result["ok"] and result["reason"] == "qty_non_positive":
            raise HTTPException(400, result["reason"])
        if not result["ok"]:
            raise HTTPException(409, result)
        for d in result["deductions"]:
            c.execute("UPDATE lots SET qty_remain = qty_remain - ? WHERE id=?", (d["take"], d["lot_id"]))
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?", (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 0:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?", (d["lot_id"],))
        c.execute("INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
                  (body.note, json.dumps(result), datetime.now(timezone.utc).isoformat()))
        c.commit()
        return result
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

class SplitIn(BaseModel):
    lot_id: int
    qty: float

@app.post("/api/split")
def split(body: SplitIn):
    """Repack qty out of one on-shelf lot into a new child lot.

    The whole order is atomic: if validation fails, or if the insert or the
    mother decrement fails, nothing is committed — no child without a mother
    and no mother decrement without its child. Quantity is conserved:
    child + mother_remain == mother_remain_before.
    """
    c = connect()
    try:
        row = c.execute("SELECT * FROM lots WHERE id=?", (body.lot_id,)).fetchone()
        lot = dict(row) if row else None
        plan = plan_split(lot, body.qty)
        if not plan["ok"]:
            raise HTTPException(_SPLIT_STATUS[plan["reason"]], plan["reason"])

        before = float(lot["qty_remain"])
        cur = c.execute(
            """INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,parent_id)
               VALUES (?,?,?,?,?,?,?)""",
            (lot["item_id"], plan["child_qty"], plan["child_qty"], lot["expiry"],
             "on_shelf", "clean", lot["id"]))
        child_id = cur.lastrowid
        # Conditional decrement: if the mother changed between read and write
        # (concurrent consume/split/expire), rowcount is 0 -> rollback. The
        # child insert above is then undone too, so no orphan can survive.
        upd = c.execute(
            "UPDATE lots SET qty_remain = qty_remain - ? WHERE id=? AND status='on_shelf' AND qty_remain=?",
            (plan["child_qty"], lot["id"], before))
        if upd.rowcount != 1:
            raise HTTPException(409, "mother_changed")
        # Post-condition the requirement demands: the mother must be reduced
        # exactly when the child is shelved, and the sum must be unchanged.
        check = c.execute(
            "SELECT (SELECT qty_remain FROM lots WHERE id=?) + (SELECT qty_remain FROM lots WHERE id=?) AS total, "
            "(SELECT qty_remain FROM lots WHERE id=?) AS mother",
            (child_id, lot["id"], lot["id"])).fetchone()
        if abs(float(check["total"]) - before) > 1e-6 or float(check["mother"]) <= 0:
            raise HTTPException(500, "split_invariant_violated")
        c.commit()
        return {
            "ok": True, "parent_id": lot["id"], "child_id": child_id,
            "child_qty": plan["child_qty"],
            "parent_remain": round(float(check["mother"]), 6),
            "total": round(float(check["total"]), 6),
        }
    except HTTPException:
        c.rollback()
        raise
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

@app.post("/api/expire-sweep")
def expire_sweep():
    c = connect()
    lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
    ids = expire_lots(lots, date.today().isoformat())
    for i in ids:
        c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    c.commit(); c.close(); return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
