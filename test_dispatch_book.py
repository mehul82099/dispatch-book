#!/usr/bin/env python3
"""Tests for dispatch_book. Run: python3 -m unittest test_dispatch_book -v"""

import contextlib
import csv
import io
import json
import os
import tempfile
import unittest

import dispatch_book as db


def book_with_carrier(name="FASTFREIGHT"):
    book = db.Book()
    book.add_carrier(name)
    return book


class ParseTest(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(db.parse_date("2026-09-01").isoformat(), "2026-09-01")
        with self.assertRaises(db.DispatchBookError):
            db.parse_date("01/09/2026")

    def test_days_between(self):
        self.assertEqual(db.days_between("2026-09-01", "2026-09-11"), 10)
        self.assertEqual(db.days_between("2026-09-11", "2026-09-01"), -10)


class CarrierTest(unittest.TestCase):
    def test_add_and_get(self):
        book = db.Book()
        book.add_carrier("FASTFREIGHT", contact="dispatch@ff.example")
        self.assertEqual(book.get_carrier("FASTFREIGHT")["contact"],
                         "dispatch@ff.example")

    def test_duplicate_carrier_rejected(self):
        book = book_with_carrier()
        with self.assertRaises(db.DispatchBookError):
            book.add_carrier("FASTFREIGHT")

    def test_empty_name_rejected(self):
        with self.assertRaises(db.DispatchBookError):
            db.Book().add_carrier("  ")

    def test_unknown_carrier_error(self):
        with self.assertRaises(db.DispatchBookError):
            db.Book().get_carrier("GHOST")


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.book = book_with_carrier()

    def add_one(self, ref="DSP-1", pickup="2026-09-01", promise="2026-09-08"):
        return self.book.add_dispatch("FASTFREIGHT", ref, "ACME PUNE",
                                      pickup, promise, goods="pumps")

    def test_add_dispatch(self):
        d = self.add_one()
        self.assertIsNone(d["picked_up"])
        self.assertIsNone(d["delivered"])

    def test_unknown_carrier_rejected(self):
        with self.assertRaises(db.DispatchBookError):
            self.book.add_dispatch("GHOST", "DSP-1", "X", "2026-09-01",
                                   "2026-09-08")

    def test_duplicate_ref_rejected(self):
        self.add_one()
        with self.assertRaises(db.DispatchBookError):
            self.add_one()

    def test_empty_ref_rejected(self):
        with self.assertRaises(db.DispatchBookError):
            self.book.add_dispatch("FASTFREIGHT", " ", "X", "2026-09-01",
                                   "2026-09-08")

    def test_promise_before_pickup_rejected(self):
        with self.assertRaises(db.DispatchBookError):
            self.add_one(pickup="2026-09-08", promise="2026-09-01")

    def test_same_day_pickup_and_promise_allowed(self):
        d = self.add_one(pickup="2026-09-05", promise="2026-09-05")
        self.assertEqual(d["promised_delivery"], "2026-09-05")


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.book = book_with_carrier()
        self.book.add_dispatch("FASTFREIGHT", "DSP-1", "ACME PUNE",
                               "2026-09-01", "2026-09-08")

    def test_pickup_on_time(self):
        d = self.book.record_pickup("DSP-1", "2026-09-01")
        self.assertEqual(db.pickup_delay(d), 0)

    def test_pickup_early_counts_on_time(self):
        d = self.book.record_pickup("DSP-1", "2026-08-30")
        self.assertEqual(db.pickup_delay(d), 0)

    def test_pickup_late(self):
        d = self.book.record_pickup("DSP-1", "2026-09-04")
        self.assertEqual(db.pickup_delay(d), 3)

    def test_double_pickup_rejected(self):
        self.book.record_pickup("DSP-1", "2026-09-01")
        with self.assertRaises(db.DispatchBookError):
            self.book.record_pickup("DSP-1", "2026-09-02")

    def test_deliver_without_pickup_rejected(self):
        with self.assertRaises(db.DispatchBookError):
            self.book.record_delivery("DSP-1", "2026-09-05", "ok")

    def test_delivery_before_pickup_rejected(self):
        self.book.record_pickup("DSP-1", "2026-09-03")
        with self.assertRaises(db.DispatchBookError):
            self.book.record_delivery("DSP-1", "2026-09-02", "ok")

    def test_double_delivery_rejected(self):
        self.book.record_pickup("DSP-1", "2026-09-01")
        self.book.record_delivery("DSP-1", "2026-09-05", "ok")
        with self.assertRaises(db.DispatchBookError):
            self.book.record_delivery("DSP-1", "2026-09-06", "ok")

    def test_bad_condition_rejected(self):
        self.book.record_pickup("DSP-1", "2026-09-01")
        with self.assertRaises(db.DispatchBookError):
            self.book.record_delivery("DSP-1", "2026-09-05", "broken")

    def test_delivery_on_time_and_late(self):
        d = self.book.record_pickup("DSP-1", "2026-09-01")
        d = self.book.record_delivery("DSP-1", "2026-09-08", "ok")
        self.assertEqual(db.delivery_delay(d), 0)
        self.book.add_dispatch("FASTFREIGHT", "DSP-2", "BETA NAGPUR",
                               "2026-09-01", "2026-09-08")
        self.book.record_pickup("DSP-2", "2026-09-01")
        d2 = self.book.record_delivery("DSP-2", "2026-09-12", "damaged",
                                       note="cartons crushed")
        self.assertEqual(db.delivery_delay(d2), 4)
        self.assertEqual(d2["delivered"]["condition"], "damaged")

    def test_unknown_dispatch_errors(self):
        with self.assertRaises(db.DispatchBookError):
            self.book.record_pickup("NOPE", "2026-09-01")
        with self.assertRaises(db.DispatchBookError):
            self.book.record_delivery("NOPE", "2026-09-01", "ok")


class OverdueTest(unittest.TestCase):
    def setUp(self):
        self.book = book_with_carrier()
        self.book.add_dispatch("FASTFREIGHT", "DSP-1", "ACME PUNE",
                               "2026-09-01", "2026-09-08")

    def test_pickup_overdue(self):
        d = self.book.get_dispatch("DSP-1")
        self.assertEqual(db.pickup_overdue_days(d, "2026-09-05"), 4)
        self.assertEqual(db.pickup_overdue_days(d, "2026-09-01"), 0)

    def test_delivery_overdue_only_in_transit(self):
        d = self.book.get_dispatch("DSP-1")
        # not picked up: no transit overdue
        self.assertEqual(db.delivery_overdue_days(d, "2026-09-20"), 0)
        self.book.record_pickup("DSP-1", "2026-09-02")
        self.assertEqual(db.delivery_overdue_days(d, "2026-09-10"), 2)
        self.assertEqual(db.delivery_overdue_days(d, "2026-09-08"), 0)
        self.book.record_delivery("DSP-1", "2026-09-12", "ok")
        self.assertEqual(db.delivery_overdue_days(d, "2026-09-20"), 0)


class StatsTest(unittest.TestCase):
    def setUp(self):
        # Two carriers with hand-computed behavior.
        self.book = book_with_carrier("ONCLOCK")
        self.book.add_carrier("SLUGLINE")
        # ONCLOCK: 2 dispatches, both picked on time, delivered on time
        for i, ref in enumerate(("OC-1", "OC-2")):
            self.book.add_dispatch("ONCLOCK", ref, "CLIENT",
                                   "2026-09-01", "2026-09-08")
            self.book.record_pickup(ref, "2026-09-01")
            self.book.record_delivery(ref, "2026-09-0%d" % (6 + i), "ok")
        # SLUGLINE: 3 dispatches
        # S-1: picked 3 late, delivered 2 late, ok
        # S-2: picked 1 late, delivered 5 late, damaged
        # S-3: awaiting pickup (scheduled 2026-09-10)
        self.book.add_dispatch("SLUGLINE", "S-1", "CLIENT", "2026-09-01",
                               "2026-09-08")
        self.book.record_pickup("S-1", "2026-09-04")
        self.book.record_delivery("S-1", "2026-09-10", "ok")
        self.book.add_dispatch("SLUGLINE", "S-2", "CLIENT", "2026-09-01",
                               "2026-09-08")
        self.book.record_pickup("S-2", "2026-09-02")
        self.book.record_delivery("S-2", "2026-09-13", "damaged")
        self.book.add_dispatch("SLUGLINE", "S-3", "CLIENT", "2026-09-10",
                               "2026-09-17")

    def test_onclock_stats(self):
        s = self.book.carrier_stats("ONCLOCK", as_of="2026-11-22")
        self.assertEqual(s["dispatches"], 2)
        self.assertEqual(s["pickup_on_time_pct"], 100)
        self.assertEqual(s["avg_pickup_delay"], 0)
        self.assertEqual(s["delivery_on_time_pct"], 100)
        self.assertEqual(s["damaged"], 0)

    def test_slugline_stats(self):
        s = self.book.carrier_stats("SLUGLINE", as_of="2026-11-22")
        self.assertEqual(s["dispatches"], 3)
        self.assertEqual(s["picked"], 2)
        self.assertEqual(s["pickup_on_time_pct"], 0)
        self.assertEqual(s["avg_pickup_delay"], 2)   # (3+1)/2
        self.assertEqual(s["worst_pickup_delay"], 3)
        self.assertEqual(s["delivered"], 2)
        self.assertEqual(s["delivery_on_time_pct"], 0)
        self.assertEqual(s["avg_delivery_delay"], 4)  # (2+5)/2 rounds to 4 (3.5 -> 4? round(3.5)=4)
        self.assertEqual(s["worst_delivery_delay"], 5)
        self.assertEqual(s["damaged"], 1)
        self.assertEqual(s["waiting"], 1)
        self.assertEqual(s["waiting_overdue"], 1)

    def test_empty_carrier_stats(self):
        self.book.add_carrier("NEWCO")
        s = self.book.carrier_stats("NEWCO")
        self.assertEqual(s["dispatches"], 0)
        self.assertIsNone(s["pickup_on_time_pct"])

    def test_pending_and_transit_lists(self):
        pending = self.book.pending_pickups("2026-11-22")
        self.assertEqual([d["ref"] for d, _ in pending], ["S-3"])
        self.book.add_dispatch("ONCLOCK", "OC-9", "CLIENT", "2026-09-02",
                               "2026-09-09")
        self.book.record_pickup("OC-9", "2026-09-02")
        transit = self.book.in_transit("2026-09-20")
        self.assertEqual([d["ref"] for d, _ in transit], ["OC-9"])
        self.assertEqual(transit[0][1], 11)  # promised 2026-09-09

    def test_summary_counts(self):
        s = self.book.summary(as_of="2026-11-22")
        self.assertEqual(s["carriers"], 2)
        self.assertEqual(s["dispatches"], 5)
        self.assertEqual(s["delivered"], 4)
        self.assertEqual(s["damaged"], 1)
        self.assertEqual(s["waiting"], 1)
        self.assertEqual(s["waiting_overdue"], 1)
        self.assertEqual(s["in_transit"], 0)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "book.json")

    def test_roundtrip(self):
        book = book_with_carrier()
        book.add_dispatch("FASTFREIGHT", "DSP-1", "ACME", "2026-09-01",
                          "2026-09-08")
        book.record_pickup("DSP-1", "2026-09-03")
        book.save(self.path)
        loaded = db.Book.load(self.path)
        d = loaded.get_dispatch("DSP-1")
        self.assertEqual(d["picked_up"]["date"], "2026-09-03")
        self.assertIsNone(d["delivered"])

    def test_load_missing_file_gives_empty_book(self):
        book = db.Book.load(os.path.join(self.tmp.name, "nope.json"))
        self.assertEqual(len(book.carriers), 0)

    def test_corrupt_file_raises_user_error(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        with self.assertRaises(db.DispatchBookError):
            db.Book.load(self.path)

    def test_save_is_atomic_replace(self):
        book = book_with_carrier()
        book.save(self.path)
        self.assertTrue(os.path.exists(self.path))
        leftovers = [n for n in os.listdir(self.tmp.name) if n != "book.json"]
        self.assertEqual(leftovers, [])
        with open(self.path, encoding="utf-8") as fh:
            json.load(fh)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "book.json")

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = db.main(["--db", self.db] + list(argv))
        return code, out.getvalue()

    def test_full_flow_and_outputs(self):
        code, out = self.run_cli("carrier", "add", "FASTFREIGHT")
        self.assertEqual(code, 0)
        self.assertIn("added carrier FASTFREIGHT", out)

        code, out = self.run_cli("add", "FASTFREIGHT", "DSP-101",
                                 "ACME PUNE", "2026-09-01", "2026-09-08",
                                 "--goods", "pump assemblies")
        self.assertEqual(code, 0)
        self.assertIn("recorded dispatch DSP-101 via FASTFREIGHT", out)

        code, out = self.run_cli("pickup", "DSP-101", "2026-09-04")
        self.assertEqual(code, 0)
        self.assertIn("3 days after schedule", out)

        code, out = self.run_cli("deliver", "DSP-101", "2026-09-12",
                                 "--damaged", "--note", "cartons crushed")
        self.assertEqual(code, 0)
        self.assertIn("4 days past promise, DAMAGED (cartons crushed)", out)

        code, out = self.run_cli("carriers")
        self.assertEqual(code, 0)
        self.assertIn("FASTFREIGHT", out)
        self.assertIn("0%", out)

        code, out = self.run_cli("carrier", "show", "FASTFREIGHT")
        self.assertEqual(code, 0)
        self.assertIn("DSP-101", out)
        self.assertIn("DAMAGED", out)

        code, out = self.run_cli("summary")
        self.assertEqual(code, 0)
        self.assertIn("carriers: 1, dispatches: 1", out)
        self.assertIn("delivered: 1 (1 damaged)", out)

    def test_cli_lifecycle_error_exit_code(self):
        self.run_cli("carrier", "add", "FASTFREIGHT")
        self.run_cli("add", "FASTFREIGHT", "DSP-1", "X", "2026-09-01",
                     "2026-09-08")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = db.main(["--db", self.db, "deliver", "DSP-1",
                            "2026-09-05"])
        self.assertEqual(code, 2)
        self.assertIn("not been picked up", err.getvalue())

    def test_cli_pending_transit_export(self):
        self.run_cli("carrier", "add", "FASTFREIGHT")
        self.run_cli("add", "FASTFREIGHT", "DSP-1", "ACME", "2026-09-01",
                     "2026-09-08")
        self.run_cli("add", "FASTFREIGHT", "DSP-2", "BETA", "2026-09-02",
                     "2026-09-09")
        self.run_cli("pickup", "DSP-2", "2026-09-02")

        code, out = self.run_cli("pending", "--as-of", "2026-09-05")
        self.assertEqual(code, 0)
        self.assertIn("DSP-1", out)
        self.assertIn("4 days past schedule", out)
        self.assertNotIn("DSP-2", out)

        code, out = self.run_cli("transit", "--as-of", "2026-09-20")
        self.assertEqual(code, 0)
        self.assertIn("DSP-2", out)
        self.assertIn("11 days past promise", out)

        csv_path = os.path.join(self.tmp.name, "out.csv")
        code, out = self.run_cli("export", csv_path)
        self.assertEqual(code, 0)
        with open(csv_path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0][:3], ["ref", "carrier", "consignee"])
        by_ref = {r[0]: r for r in rows[1:]}
        self.assertEqual(by_ref["DSP-1"][6], "")          # no pickup yet
        self.assertEqual(by_ref["DSP-2"][6], "2026-09-02")
        self.assertEqual(by_ref["DSP-2"][7], "0")         # picked on time


if __name__ == "__main__":
    unittest.main()
