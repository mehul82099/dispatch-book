#!/usr/bin/env python3
"""dispatch-book: zero-dependency outbound pickup accountability CLI for
small manufacturers.

Record each dispatch with its carrier, scheduled pickup date and promised
delivery date. Log the actual pickup and delivery (with a damage flag).
The book answers: which carriers actually show up when promised, how late
the rest are, what is still waiting for pickup, what is in transit past
its promised date, and how often goods arrive damaged - so the next
carrier conversation starts from your own record, not memory.

Python 3.9+, standard library only. One JSON file, offline, no accounts.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "dispatch_book.json")

CONDITIONS = ("ok", "damaged")


class DispatchBookError(Exception):
    """User-facing error (bad input, unknown ref, ...)."""


def today_iso():
    return date.today().isoformat()


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise DispatchBookError("bad date %r (expected YYYY-MM-DD)" % text)


def days_between(a_iso, b_iso):
    return (parse_date(b_iso) - parse_date(a_iso)).days


def pickup_delay(disp):
    """Days the actual pickup was after the scheduled pickup (0 if early/on time)."""
    if not disp["picked_up"]:
        return None
    return max(0, days_between(disp["scheduled_pickup"],
                               disp["picked_up"]["date"]))


def delivery_delay(disp):
    """Days the actual delivery was after the promised date (0 if early/on time)."""
    if not disp["delivered"]:
        return None
    return max(0, days_between(disp["promised_delivery"],
                               disp["delivered"]["date"]))


def pickup_overdue_days(disp, as_of=None):
    """Days past scheduled pickup for a dispatch not yet picked up."""
    if disp["picked_up"]:
        return 0
    as_of = parse_date(as_of) if as_of else date.today()
    return max(0, (as_of - parse_date(disp["scheduled_pickup"])).days)


def delivery_overdue_days(disp, as_of=None):
    """Days past promised delivery for a dispatch picked up but not delivered."""
    if disp["delivered"] or not disp["picked_up"]:
        return 0
    as_of = parse_date(as_of) if as_of else date.today()
    return max(0, (as_of - parse_date(disp["promised_delivery"])).days)


class Book:
    def __init__(self):
        self.carriers = {}    # name -> {name, contact, created}
        self.dispatches = {}  # ref  -> dispatch dict

    @classmethod
    def load(cls, path):
        book = cls()
        if not os.path.exists(path):
            return book
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise DispatchBookError("cannot read %s: %s" % (path, exc))
        for name, carrier in data.get("carriers", {}).items():
            book.carriers[name] = carrier
        for ref, disp in data.get("dispatches", {}).items():
            book.dispatches[ref] = disp
        return book

    def save(self, path):
        data = {"carriers": self.carriers, "dispatches": self.dispatches}
        fd, tmp_name = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- carriers -------------------------------------------------------

    def add_carrier(self, name, contact=""):
        name = (name or "").strip()
        if not name:
            raise DispatchBookError("carrier name cannot be empty")
        if name in self.carriers:
            raise DispatchBookError("carrier %r already exists" % name)
        self.carriers[name] = {"name": name, "contact": contact,
                               "created": today_iso()}
        return self.carriers[name]

    def get_carrier(self, name):
        try:
            return self.carriers[name]
        except KeyError:
            raise DispatchBookError("unknown carrier %r (add it first)"
                                    % (name,))

    # -- dispatches -----------------------------------------------------

    def add_dispatch(self, carrier, ref, consignee, scheduled_pickup,
                     promised_delivery, goods=""):
        self.get_carrier(carrier)
        ref = (ref or "").strip()
        if not ref:
            raise DispatchBookError("dispatch ref cannot be empty")
        if ref in self.dispatches:
            raise DispatchBookError("dispatch ref %r already exists" % ref)
        if parse_date(promised_delivery) < parse_date(scheduled_pickup):
            raise DispatchBookError(
                "promised delivery %s is before scheduled pickup %s"
                % (promised_delivery, scheduled_pickup))
        disp = {"ref": ref, "carrier": carrier, "consignee": consignee,
                "goods": goods, "scheduled_pickup": scheduled_pickup,
                "promised_delivery": promised_delivery,
                "picked_up": None, "delivered": None}
        self.dispatches[ref] = disp
        return disp

    def get_dispatch(self, ref):
        try:
            return self.dispatches[ref]
        except KeyError:
            raise DispatchBookError("unknown dispatch %r" % (ref,))

    def record_pickup(self, ref, when):
        disp = self.get_dispatch(ref)
        if disp["picked_up"]:
            raise DispatchBookError("dispatch %s was already picked up on %s"
                                    % (ref, disp["picked_up"]["date"]))
        if disp["delivered"]:
            raise DispatchBookError("dispatch %s is already delivered" % ref)
        disp["picked_up"] = {"date": when}
        return disp

    def record_delivery(self, ref, when, condition, note=""):
        disp = self.get_dispatch(ref)
        if not disp["picked_up"]:
            raise DispatchBookError(
                "dispatch %s has not been picked up yet; record the pickup "
                "first" % ref)
        if disp["delivered"]:
            raise DispatchBookError("dispatch %s was already delivered on %s"
                                    % (ref, disp["delivered"]["date"]))
        if parse_date(when) < parse_date(disp["picked_up"]["date"]):
            raise DispatchBookError(
                "delivery date %s is before pickup date %s"
                % (when, disp["picked_up"]["date"]))
        if condition not in CONDITIONS:
            raise DispatchBookError("condition must be one of: %s"
                                    % ", ".join(CONDITIONS))
        disp["delivered"] = {"date": when, "condition": condition,
                             "note": note}
        return disp

    # -- reporting ------------------------------------------------------

    def carrier_dispatches(self, name):
        return [d for d in self.dispatches.values() if d["carrier"] == name]

    def carrier_stats(self, name, as_of=None):
        disps = self.carrier_dispatches(name)
        picked = [d for d in disps if d["picked_up"]]
        p_on_time = [d for d in picked if pickup_delay(d) == 0]
        p_delays = [pickup_delay(d) for d in picked if pickup_delay(d) > 0]
        done = [d for d in disps if d["delivered"]]
        d_on_time = [d for d in done if delivery_delay(d) == 0]
        d_delays = [delivery_delay(d) for d in done if delivery_delay(d) > 0]
        damaged = [d for d in done if d["delivered"]["condition"] == "damaged"]
        waiting = [d for d in disps if not d["picked_up"]]
        transit = [d for d in disps
                   if d["picked_up"] and not d["delivered"]]
        return {
            "dispatches": len(disps),
            "picked": len(picked),
            "pickup_on_time_pct": (round(100.0 * len(p_on_time) / len(picked))
                                   if picked else None),
            "avg_pickup_delay": (round(sum(p_delays) / len(p_delays))
                                 if p_delays else 0),
            "worst_pickup_delay": max(p_delays) if p_delays else 0,
            "delivered": len(done),
            "delivery_on_time_pct": (round(100.0 * len(d_on_time) / len(done))
                                     if done else None),
            "avg_delivery_delay": (round(sum(d_delays) / len(d_delays))
                                   if d_delays else 0),
            "worst_delivery_delay": max(d_delays) if d_delays else 0,
            "damaged": len(damaged),
            "waiting": len(waiting),
            "waiting_overdue": sum(1 for d in waiting
                                   if pickup_overdue_days(d, as_of) > 0),
            "in_transit": len(transit),
            "transit_overdue": sum(1 for d in transit
                                   if delivery_overdue_days(d, as_of) > 0),
        }

    def pending_pickups(self, as_of=None):
        rows = [(d, pickup_overdue_days(d, as_of))
                for d in self.dispatches.values() if not d["picked_up"]]
        rows.sort(key=lambda row: (row[0]["scheduled_pickup"], row[0]["ref"]))
        return rows

    def in_transit(self, as_of=None):
        rows = [(d, delivery_overdue_days(d, as_of))
                for d in self.dispatches.values()
                if d["picked_up"] and not d["delivered"]]
        rows.sort(key=lambda row: (row[0]["promised_delivery"], row[0]["ref"]))
        return rows

    def summary(self, as_of=None):
        s = {"carriers": len(self.carriers),
             "dispatches": len(self.dispatches),
             "waiting": 0, "waiting_overdue": 0,
             "in_transit": 0, "transit_overdue": 0, "delivered": 0,
             "damaged": 0}
        for d in self.dispatches.values():
            if d["delivered"]:
                s["delivered"] += 1
                if d["delivered"]["condition"] == "damaged":
                    s["damaged"] += 1
            elif d["picked_up"]:
                s["in_transit"] += 1
                if delivery_overdue_days(d, as_of) > 0:
                    s["transit_overdue"] += 1
            else:
                s["waiting"] += 1
                if pickup_overdue_days(d, as_of) > 0:
                    s["waiting_overdue"] += 1
        return s

    def export_csv(self, out_path, as_of=None):
        fields = ["ref", "carrier", "consignee", "goods", "scheduled_pickup",
                  "promised_delivery", "pickup_date", "pickup_delay_days",
                  "delivery_date", "delivery_delay_days", "condition",
                  "note"]
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(fields)
            for d in self.dispatches.values():
                pd = pickup_delay(d)
                dd = delivery_delay(d)
                writer.writerow([
                    d["ref"], d["carrier"], d["consignee"], d["goods"],
                    d["scheduled_pickup"], d["promised_delivery"],
                    d["picked_up"]["date"] if d["picked_up"] else "",
                    pd if pd is not None else "",
                    d["delivered"]["date"] if d["delivered"] else "",
                    dd if dd is not None else "",
                    d["delivered"]["condition"] if d["delivered"] else "",
                    d["delivered"]["note"] if d["delivered"] else "",
                ])


def _pct(value):
    return "-" if value is None else "%d%%" % value


def cmd_carrier_add(book, args):
    book.add_carrier(args.name, contact=args.contact or "")
    print("added carrier %s" % args.name)


def cmd_add(book, args):
    d = book.add_dispatch(args.carrier, args.ref, args.consignee,
                          args.pickup_date, args.delivery_date,
                          goods=args.goods or "")
    print("recorded dispatch %s via %s to %s: pickup scheduled %s, "
          "delivery promised %s"
          % (d["ref"], d["carrier"], d["consignee"], d["scheduled_pickup"],
             d["promised_delivery"]))


def cmd_pickup(book, args):
    d = book.record_pickup(args.ref, args.date)
    delay = pickup_delay(d)
    note = "on time" if delay == 0 else "%d days after schedule" % delay
    print("recorded pickup of %s on %s (%s)" % (args.ref, args.date, note))


def cmd_deliver(book, args):
    condition = "damaged" if args.damaged else "ok"
    d = book.record_delivery(args.ref, args.date, condition,
                             note=args.note or "")
    delay = delivery_delay(d)
    timing = "on time" if delay == 0 else "%d days past promise" % delay
    state = "" if condition == "ok" else ", DAMAGED"
    if d["delivered"]["note"]:
        state += " (%s)" % d["delivered"]["note"]
    print("recorded delivery of %s on %s (%s%s)"
          % (args.ref, args.date, timing, state))


def cmd_carriers(book, args):
    if not book.carriers:
        print("no carriers yet")
        return
    print("%-22s %10s %14s %10s %14s %10s %8s" % (
        "carrier", "dispatches", "pickup on-time", "avg late",
        "delivery on-time", "avg late", "damaged"))
    for name in sorted(book.carriers):
        s = book.carrier_stats(name, args.as_of)
        print("%-22s %10d %14s %10s %14s %10s %8d" % (
            name, s["dispatches"], _pct(s["pickup_on_time_pct"]),
            s["avg_pickup_delay"] if s["picked"] else "-",
            _pct(s["delivery_on_time_pct"]),
            s["avg_delivery_delay"] if s["delivered"] else "-",
            s["damaged"]))


def cmd_carrier(book, args):
    book.get_carrier(args.name)
    disps = book.carrier_dispatches(args.name)
    if not disps:
        print("%s: no dispatches recorded" % args.name)
        return
    print("%s" % args.name)
    for d in sorted(disps, key=lambda x: (x["scheduled_pickup"], x["ref"])):
        line = "  %s  to %-18s  pickup %s  promise %s" % (
            d["ref"], d["consignee"], d["scheduled_pickup"],
            d["promised_delivery"])
        if d["delivered"]:
            delay = delivery_delay(d)
            timing = "on time" if delay == 0 else "%d late" % delay
            state = "delivered %s, %s" % (d["delivered"]["date"], timing)
            if d["delivered"]["condition"] == "damaged":
                state += ", DAMAGED"
        elif d["picked_up"]:
            delay = pickup_delay(d)
            timing = "on time" if delay == 0 else "%d late" % delay
            over = delivery_overdue_days(d, args.as_of)
            tail = "in transit" if over == 0 else \
                   "in transit, %d days past promise" % over
            state = "picked %s (%s), %s" % (d["picked_up"]["date"], timing,
                                            tail)
        else:
            over = pickup_overdue_days(d, args.as_of)
            state = "awaiting pickup" if over == 0 else \
                    "awaiting pickup, %d days past schedule" % over
        print("%s  %s" % (line, state))


def cmd_pending(book, args):
    rows = book.pending_pickups(args.as_of)
    if not rows:
        print("nothing waiting for pickup")
        return
    as_of = args.as_of or today_iso()
    print("awaiting pickup as of %s:" % as_of)
    for d, over in rows:
        state = "scheduled %s" % d["scheduled_pickup"] if over == 0 else \
                "%d days past schedule" % over
        print("  %-10s %-22s to %-18s  %s" % (
            d["ref"], d["carrier"], d["consignee"], state))


def cmd_transit(book, args):
    rows = book.in_transit(args.as_of)
    if not rows:
        print("nothing in transit")
        return
    as_of = args.as_of or today_iso()
    print("in transit as of %s:" % as_of)
    for d, over in rows:
        state = "promised %s" % d["promised_delivery"] if over == 0 else \
                "%d days past promise" % over
        print("  %-10s %-22s to %-18s  picked %s, %s" % (
            d["ref"], d["carrier"], d["consignee"], d["picked_up"]["date"],
            state))


def cmd_export(book, args):
    book.export_csv(args.path, args.as_of)
    print("wrote %s" % args.path)


def cmd_summary(book, args):
    s = book.summary(args.as_of)
    print("carriers: %d, dispatches: %d" % (s["carriers"], s["dispatches"]))
    print("  awaiting pickup: %d (%d past schedule)"
          % (s["waiting"], s["waiting_overdue"]))
    print("  in transit: %d (%d past promise)"
          % (s["in_transit"], s["transit_overdue"]))
    print("  delivered: %d (%d damaged)" % (s["delivered"], s["damaged"]))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="dispatch-book",
        description="Outbound pickup accountability book: which carriers "
                    "show up on time, how late the rest are, and what "
                    "arrived damaged.")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help="JSON data file (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_as_of(p):
        p.add_argument("--as-of", default=None,
                       help="compute overdue days as of YYYY-MM-DD "
                            "(default: today)")

    p = sub.add_parser("carrier", help="carrier add / statement")
    carrier_sub = p.add_subparsers(dest="carrier_cmd", required=True)
    pa = carrier_sub.add_parser("add", help="add a carrier")
    pa.add_argument("name")
    pa.add_argument("--contact", default=None)
    pa.set_defaults(func=cmd_carrier_add)
    ps = carrier_sub.add_parser("show", help="one carrier's dispatch log")
    ps.add_argument("name")
    add_as_of(ps)
    ps.set_defaults(func=cmd_carrier)

    p = sub.add_parser("add", help="record an outbound dispatch")
    p.add_argument("carrier")
    p.add_argument("ref")
    p.add_argument("consignee", help="who receives the goods")
    p.add_argument("pickup_date", help="scheduled pickup, YYYY-MM-DD")
    p.add_argument("delivery_date", help="promised delivery, YYYY-MM-DD")
    p.add_argument("--goods", default=None, help="what is being sent")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("pickup", help="record the actual pickup date")
    p.add_argument("ref")
    p.add_argument("date", help="actual pickup date, YYYY-MM-DD")
    p.set_defaults(func=cmd_pickup)

    p = sub.add_parser("deliver", help="record the actual delivery")
    p.add_argument("ref")
    p.add_argument("date", help="actual delivery date, YYYY-MM-DD")
    p.add_argument("--damaged", action="store_true",
                   help="mark the consignment as delivered damaged")
    p.add_argument("--note", default=None, help="free-text note")
    p.set_defaults(func=cmd_deliver)

    p = sub.add_parser("carriers", help="per-carrier scorecard table")
    add_as_of(p)
    p.set_defaults(func=cmd_carriers)

    p = sub.add_parser("pending", help="dispatches still awaiting pickup")
    add_as_of(p)
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("transit", help="picked up but not yet delivered")
    add_as_of(p)
    p.set_defaults(func=cmd_transit)

    p = sub.add_parser("export", help="export all dispatches to CSV")
    p.add_argument("path")
    add_as_of(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("summary", help="one-glance totals")
    add_as_of(p)
    p.set_defaults(func=cmd_summary)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        book = Book.load(args.db)
        args.func(book, args)
        book.save(args.db)
    except DispatchBookError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
