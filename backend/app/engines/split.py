"""Repacking / splitting one on-shelf mother lot into a child lot.

Pure planning layer: validates with the exact same eligibility gate the
consume engine uses, then returns the quantity plan. Persistence (insert
child, decrement mother, rollback on failure) lives in the API layer.
"""

from app.engines.fefo import EPS, is_eligible

# Reasons returned on failure; the API maps them to status codes.
REASONS = (
    "lot_not_found",
    "lot_not_eligible",
    "split_qty_non_positive",
    "split_qty_exceeds_remain",
)


def plan_split(lot: dict | None, split_qty: float) -> dict:
    """Validate a split of split_qty out of a mother lot.

    The child quantity must be strictly between 0 and the mother remainder:
    zero is meaningless and a full-size "split" would erase the mother row
    (its id would disappear from the shelf), breaking FEFO history that may
    point at it. The mother always survives with remainder - split_qty > 0.
    """
    if lot is None:
        return {"ok": False, "reason": "lot_not_found"}
    if not is_eligible(lot):
        # Dirty lots and negative-remainder rows: same verdict consume gives.
        return {"ok": False, "reason": "lot_not_eligible"}
    qty = float(split_qty)
    if qty <= EPS:
        return {"ok": False, "reason": "split_qty_non_positive"}
    remain = float(lot["qty_remain"])
    if qty > remain + EPS or remain - qty <= EPS:
        return {"ok": False, "reason": "split_qty_exceeds_remain"}
    child_qty = round(qty, 6)
    mother_after = round(remain - qty, 6)
    return {
        "ok": True,
        "reason": "",
        "lot_id": lot["id"],
        "item_id": lot["item_id"],
        "expiry": lot.get("expiry"),
        "child_qty": child_qty,
        "mother_before": round(remain, 6),
        "mother_after": mother_after,
        "total_after": round(child_qty + mother_after, 6),
    }
