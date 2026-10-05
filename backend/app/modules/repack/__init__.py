"""在架分装: split an on-shelf mother lot into a child lot.

Invariants:
- Preview never mutates; only confirm writes.
- After confirm: child.qty_remain + mother.qty_remain == mother.qty_remain before.
- The mother row is never deleted (its id stays resolvable for consumption
  history); a fully split mother leaves the shelf like a depleted lot does.
- Eligibility is the same rule as FEFO consumption candidates
  (engines.fefo.is_consumable): dirty lots with positive remain may split,
  negative-remain rows may not.
- Confirm is one BEGIN IMMEDIATE transaction that re-reads the mother row
  before writing, so an overlapping consume / expire-sweep on the same mother
  cannot produce a child on shelf with an undeducted mother, nor a stale
  deduction landing on the old mother id. Any failure rolls back cleanly —
  no orphan child rows.
"""
from app.engines.fefo import is_consumable

def plan_split(lot: dict | None, qty: float) -> dict:
    """Pure preview of a split. Never mutates anything."""
    if lot is None:
        return {"ok": False, "reason": "lot_not_found"}
    if not is_consumable(lot):
        return {"ok": False, "reason": "lot_not_splittable"}
    try:
        take = float(qty)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "qty_non_positive"}
    if not take > 0:  # covers <= 0 and NaN
        return {"ok": False, "reason": "qty_non_positive"}
    remain = float(lot["qty_remain"])
    if take > remain + 1e-9:
        return {"ok": False, "reason": "qty_exceeds_remain", "remain": remain}
    take = min(take, remain)  # absorb float dust at the boundary
    return {
        "ok": True,
        "reason": "",
        "mother": {
            "id": lot["id"],
            "qty_remain_before": remain,
            "qty_remain_after": round(remain - take, 6),
        },
        "child": {
            "item_id": lot["item_id"],
            "qty": take,
            "expiry": lot.get("expiry"),
            "data_quality": lot.get("data_quality"),
            "parent_id": lot["id"],
        },
    }

def _load_lot(conn, lot_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM lots WHERE id=?", (lot_id,)).fetchone()
    return dict(row) if row else None

def preview_split(conn, lot_id: int, qty: float) -> dict:
    """Read-only plan against current DB state. Writes nothing."""
    return plan_split(_load_lot(conn, lot_id), qty)

def confirm_split(conn, lot_id: int, qty: float) -> dict:
    """Apply a split atomically. Returns the plan; on failure the plan has
    ok=False and the DB is left exactly as before the call."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        plan = plan_split(_load_lot(conn, lot_id), qty)
        if not plan["ok"]:
            conn.rollback()
            return plan
        after = plan["mother"]["qty_remain_after"]
        if after <= 0:
            # fully split: mother leaves the shelf like a depleted lot
            conn.execute("UPDATE lots SET qty_remain=0, status='consumed' WHERE id=?", (lot_id,))
        else:
            conn.execute("UPDATE lots SET qty_remain=? WHERE id=?", (after, lot_id))
        ch = plan["child"]
        cur = conn.execute(
            "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality,parent_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (ch["item_id"], ch["qty"], ch["qty"], ch["expiry"], "on_shelf",
             ch["data_quality"], ch["parent_id"]))
        plan["child"]["id"] = cur.lastrowid
        conn.commit()
        return plan
    except Exception:
        conn.rollback()
        raise
