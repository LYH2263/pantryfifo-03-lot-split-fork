"""在架分装 tests: preview purity, conservation, eligibility parity with
FEFO candidates, atomicity under interleaved consume/expire, and history
idempotency. Stdlib-only so they run under pytest and `python3 -m unittest`."""
import json
import os
import tempfile
import unittest
from datetime import date

from app import seed
from app.db import connect
from app.engines.fefo import consume_fefo, expire_lots, is_consumable, sort_lots_fefo
from app.modules.repack import confirm_split, plan_split, preview_split


class RepackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name
        seed.init_db()
        self.c = connect()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    # --- helpers ---------------------------------------------------------
    def lots(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM lots")]

    def lot(self, lid):
        return dict(self.c.execute("SELECT * FROM lots WHERE id=?", (lid,)).fetchone())

    def shelf_total(self):
        return sum(float(r["qty_remain"]) for r in self.c.execute(
            "SELECT qty_remain FROM lots WHERE status='on_shelf'"))

    def orphan_count(self):
        return self.c.execute(
            "SELECT COUNT(*) n FROM lots WHERE parent_id IS NOT NULL"
            " AND parent_id NOT IN (SELECT id FROM lots)").fetchone()["n"]

    def apply_consume(self, item_id, qty, note="t"):
        """Same deduction flow as the /api/consume endpoint."""
        lots = [dict(r) for r in self.c.execute(
            "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0", (item_id,))]
        res = consume_fefo(lots, qty)
        assert res["ok"], res
        for d in res["deductions"]:
            self.c.execute("UPDATE lots SET qty_remain = qty_remain - ? WHERE id=?",
                           (d["take"], d["lot_id"]))
            rem = self.c.execute("SELECT qty_remain FROM lots WHERE id=?",
                                 (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 0:
                self.c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?",
                               (d["lot_id"],))
        self.c.execute(
            "INSERT INTO consumptions(note,result_json,created_at) VALUES (?,?,?)",
            (note, json.dumps(res), "2026-10-05T00:00:00+00:00"))
        self.c.commit()
        return res

    # --- eligibility: one rule for consume candidates and splits ---------
    def test_is_consumable_rule(self):
        self.assertTrue(is_consumable({"qty_remain": 1, "status": "on_shelf", "data_quality": "dirty"}))
        self.assertFalse(is_consumable({"qty_remain": -1, "status": "on_shelf"}))
        self.assertFalse(is_consumable({"qty_remain": 0, "status": "on_shelf"}))
        self.assertFalse(is_consumable({"qty_remain": 1, "status": "consumed"}))

    def test_split_eligibility_matches_consume_candidates(self):
        lots = self.lots()
        candidates = {l["id"] for l in sort_lots_fefo(lots)}
        for l in lots:
            self.assertEqual(plan_split(l, 0.5)["ok"], l["id"] in candidates, l)
        # dirty but positive (seed lot 4) may split; dirty negative (lot 5) may not
        self.assertTrue(plan_split(self.lot(4), 0.5)["ok"])
        bad = plan_split(self.lot(5), 0.5)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["reason"], "lot_not_splittable")

    # --- preview ---------------------------------------------------------
    def test_preview_does_not_mutate(self):
        before, n0 = self.lot(1), len(self.lots())
        plan = preview_split(self.c, 1, 0.5)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["mother"]["qty_remain_after"], 1.5)
        self.assertEqual(self.lot(1), before)
        self.assertEqual(len(self.lots()), n0)

    # --- confirm: conservation + child identity --------------------------
    def test_confirm_conserves_quantity(self):
        t0 = self.shelf_total()
        plan = confirm_split(self.c, 1, 0.5)
        self.assertTrue(plan["ok"])
        mother, child = self.lot(1), self.lot(plan["child"]["id"])
        self.assertEqual(mother["qty_remain"] + child["qty_remain"], 2)  # pre-split remain
        self.assertEqual(child["parent_id"], 1)
        self.assertEqual(child["status"], "on_shelf")
        self.assertEqual(child["item_id"], mother["item_id"])
        self.assertEqual(child["expiry"], mother["expiry"])
        self.assertEqual(self.shelf_total(), t0)  # 全层/层页加总不变
        self.assertEqual(self.orphan_count(), 0)

    def test_fefo_hits_child_row_mother_id_not_vanished(self):
        plan = confirm_split(self.c, 1, 1.5)  # mother 0.5, child 1.5, same expiry
        child_id = plan["child"]["id"]
        lots = [dict(r) for r in self.c.execute(
            "SELECT * FROM lots WHERE item_id=1 AND status='on_shelf'")]
        res = consume_fefo(lots, 2.5)
        self.assertTrue(res["ok"])
        # FEFO: lot2 (09-28 ×1) → lot1 (10-01 ×0.5) → child (10-01 ×1.0)
        self.assertEqual([d["lot_id"] for d in res["deductions"]], [2, 1, child_id])
        self.assertEqual(self.lot(1)["id"], 1)  # mother row still resolves

    def test_full_split_retires_mother_keeps_id(self):
        plan = confirm_split(self.c, 2, 1)  # lot2 remain 1, split all
        self.assertTrue(plan["ok"])
        mother = self.lot(2)
        self.assertEqual((mother["qty_remain"], mother["status"]), (0, "consumed"))
        child = self.lot(plan["child"]["id"])
        self.assertEqual((child["qty_remain"], child["parent_id"]), (1, 2))
        lots = [dict(r) for r in self.c.execute(
            "SELECT * FROM lots WHERE item_id=1 AND status='on_shelf'")]
        res = consume_fefo(lots, 1)  # child has earliest expiry now
        self.assertEqual(res["deductions"][0]["lot_id"], child["id"])

    # --- invalid qty: whole request fails, nothing left behind -----------
    def test_zero_and_over_qty_fail_clean(self):
        n0, t0 = len(self.lots()), self.shelf_total()
        r = confirm_split(self.c, 1, 0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "qty_non_positive")
        r = confirm_split(self.c, 1, 99)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "qty_exceeds_remain")
        r = confirm_split(self.c, 9999, 1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "lot_not_found")
        self.assertEqual(len(self.lots()), n0)          # no orphan rows
        self.assertEqual(self.shelf_total(), t0)        # back to pre-split
        self.assertEqual(self.lot(1)["qty_remain"], 2)
        self.assertEqual(self.orphan_count(), 0)

    # --- overlap with consume on the same mother -------------------------
    def test_confirm_rereads_mother_after_interleaved_consume(self):
        plan = preview_split(self.c, 3, 5)  # lot3 remain 12
        self.assertTrue(plan["ok"])
        self.apply_consume(2, 7)  # same mother depleted 12 → 5 in between
        plan2 = confirm_split(self.c, 3, 5)
        self.assertTrue(plan2["ok"])
        self.assertEqual(plan2["mother"]["qty_remain_after"], 0)
        child = self.lot(plan2["child"]["id"])
        # conserved against the post-consume state, not the stale preview
        self.assertEqual(self.lot(3)["qty_remain"] + child["qty_remain"], 5)
        self.assertEqual(self.orphan_count(), 0)

    def test_confirm_fails_clean_when_interleaved_consume_depleted(self):
        self.assertTrue(preview_split(self.c, 3, 5)["ok"])
        self.apply_consume(2, 10)  # lot3 remain 12 → 2
        n0 = len(self.lots())
        r = confirm_split(self.c, 3, 5)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "qty_exceeds_remain")
        self.assertEqual(self.lot(3)["qty_remain"], 2)  # untouched
        self.assertEqual(len(self.lots()), n0)          # no child on shelf
        self.assertEqual(self.orphan_count(), 0)

    # --- overlap with expire-sweep ---------------------------------------
    def test_expire_sweep_after_split_hits_both_rows(self):
        plan = confirm_split(self.c, 4, 0.5)  # dirty lot, expiry 2025-01-01
        child_id = plan["child"]["id"]
        lots = [dict(r) for r in self.c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
        ids = expire_lots(lots, date.today().isoformat())
        self.assertIn(4, ids)
        self.assertIn(child_id, ids)  # child inherits mother's expiry
        for i in ids:
            self.c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
        self.c.commit()
        self.assertEqual(self.lot(child_id)["status"], "expired")
        self.assertEqual(self.orphan_count(), 0)

    # --- consumption history must not re-deduct the child ----------------
    def test_consumption_history_not_replayed_on_child(self):
        res = self.apply_consume(1, 1.5)  # records mother lot_id 1 (take 0.5)
        self.assertTrue(any(d["lot_id"] == 1 for d in res["deductions"]))
        plan = confirm_split(self.c, 1, 0.5)  # mother 1.5 → 1.0, child 0.5
        child_id = plan["child"]["id"]
        rec = json.loads(self.c.execute(
            "SELECT result_json FROM consumptions ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(rec, res)  # history record untouched by the split
        self.assertTrue(any(d["lot_id"] == 1 for d in rec["deductions"]))
        self.assertFalse(any(d["lot_id"] == child_id for d in rec["deductions"]))
        # child keeps exactly the split qty: the old record is not re-applied
        self.assertEqual(self.lot(child_id)["qty_remain"], 0.5)
        self.assertEqual(self.lot(1)["qty_remain"], 1.0)


if __name__ == "__main__":
    unittest.main()
