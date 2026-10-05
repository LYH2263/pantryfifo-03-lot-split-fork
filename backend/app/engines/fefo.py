"""FEFO consume: earliest expiry first among eligible positive remaining lots.

Eligibility (the single source of truth shared by consume, expiry-driven
picking and repacking/splitting): a lot must be on the shelf, have a
strictly positive remaining quantity and be clean. Dirty lots and
negative-remainder rows never enter the candidate set and therefore cannot
be split either.
"""

EPS = 1e-9


def is_eligible(lot: dict) -> bool:
    """Same gate for consume candidates and split sources."""
    return (
        lot.get("status") == "on_shelf"
        and float(lot.get("qty_remain", 0) or 0) > 0
        and lot.get("data_quality") == "clean"
    )


def sort_lots_fefo(lots: list[dict]) -> list[dict]:
    def key(l):
        expiry = l.get("expiry") or "9999-99-99"
        # Same expiry: repacked child rows are taken before their mother.
        # A split-out package is meant to be used first, and the mother may
        # already be partially gone; ranking children first guarantees a
        # consume lands on the rows the shelf actually shows now.
        rank = 0 if l.get("parent_id") is not None else 1
        return (expiry, rank, l.get("id") or 0)

    return sorted([l for l in lots if is_eligible(l)], key=key)

def consume_fefo(lots: list[dict], qty: float) -> dict:
    """Return deductions list and leftover demand. Mutates copies only."""
    need = float(qty)
    if need <= 0:
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
    if need > EPS:
        return {"ok": False, "reason": "short", "deductions": deductions, "short": round(need, 3)}
    return {"ok": True, "reason": "", "deductions": deductions, "short": 0.0}

def expire_lots(lots: list[dict], today: str) -> list[int]:
    """Ids that should leave shelf: remaining>0 and expiry < today."""
    out = []
    for l in lots:
        exp = l.get("expiry")
        if exp and exp < today and float(l.get("qty_remain", 0) or 0) > 0:
            out.append(l["id"])
    return out
