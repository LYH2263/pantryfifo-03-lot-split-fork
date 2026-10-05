"""FEFO consume: earliest expiry first among positive remaining lots."""

def is_consumable(lot: dict) -> bool:
    """Single eligibility rule shared by FEFO candidates and repack splits.

    A lot qualifies iff it is on shelf with positive remaining qty.
    data_quality is deliberately NOT part of the rule: dirty lots with
    positive remain are consumable, hence also splittable; negative-remain
    rows are neither. Missing status (engine-level dicts) reads as on_shelf.
    """
    if float(lot.get("qty_remain", 0)) <= 0:
        return False
    return lot.get("status", "on_shelf") == "on_shelf"

def sort_lots_fefo(lots: list[dict]) -> list[dict]:
    return sorted(
        [l for l in lots if is_consumable(l)],
        key=lambda l: (l.get("expiry") or "9999-99-99", l.get("id") or 0),
    )

def consume_fefo(lots: list[dict], qty: float) -> dict:
    """Return deductions list and leftover demand. Mutates copies only."""
    need = float(qty)
    if not need > 0:  # covers <= 0 and NaN
        return {"ok": False, "reason": "qty_non_positive", "deductions": [], "short": 0.0}
    ordered = sort_lots_fefo(lots)
    deductions = []
    for lot in ordered:
        if need <= 0:
            break
        avail = float(lot["qty_remain"])
        take = min(avail, need)
        deductions.append({"lot_id": lot["id"], "take": take, "expiry": lot.get("expiry")})
        need -= take
    if need > 1e-9:
        return {"ok": False, "reason": "short", "deductions": deductions, "short": round(need, 3)}
    return {"ok": True, "reason": "", "deductions": deductions, "short": 0.0}

def expire_lots(lots: list[dict], today: str) -> list[int]:
    """Ids that should leave shelf: remaining>0 and expiry < today."""
    out = []
    for l in lots:
        exp = l.get("expiry")
        if exp and exp < today and float(l.get("qty_remain", 0)) > 0:
            out.append(l["id"])
    return out
