# dispatch-book

A zero-dependency command-line outbound pickup accountability book for
small manufacturers. Record each dispatch with its carrier, scheduled
pickup date and promised delivery date; log the actual pickup and
delivery (with a damage flag). The book answers: which carriers actually
show up when promised, how late the rest are, what is still waiting for
pickup, what is in transit past its promised date, and how often goods
arrive damaged - so the next carrier conversation starts from your own
record, not memory.

Single Python file, standard library only (Python 3.9+). Fully offline;
all data lives in one local JSON file you own.

## Why this exists

On Razorpay's public "Fix My Itch" list, one entry asks: "Why can't
manufacturers rely on guaranteed, accountable pickup logistics for
outbound deliveries?" (page fetched and entry re-verified on 2026-11-22;
https://razorpay.com/m/fix-my-itch/). That list is curated by Razorpay -
it shows the problem was submitted and published, not how common it is or
that a tool like this is in demand.

No tool can guarantee a truck shows up. What a small manufacturer can
have is an accountability record: dated, per carrier, covering scheduled
vs actual pickups, promised vs actual deliveries, and damaged arrivals.
That record is what this book keeps - the difference between "you are
always late" and "your last 8 pickups averaged 3 days past schedule".

## What it does

- Carriers: add each carrier once, with an optional contact note.
- Dispatches: record ref, consignee, goods, scheduled pickup date and
  promised delivery date (a promise earlier than the pickup is rejected).
- Events: `pickup` records the actual pickup date; `deliver` records the
  actual delivery date, an optional `--damaged` flag and a note. Delivery
  before pickup, double pickup and double delivery are all rejected.
- Scorecard (`carriers`): per carrier - dispatches, pickup on-time %,
  average pickup delay, delivery on-time %, average delivery delay,
  damaged count.
- Log (`carrier show`): every dispatch with what actually happened.
- Queues: `pending` (awaiting pickup, days past schedule) and `transit`
  (picked up, not delivered, days past promise), each with `--as-of`.
- Export (`export`): all dispatches to CSV.
- Summary (`summary`): one-glance totals across the book.

## Install and run

Requires Python 3.9 or newer. Nothing else.

```sh
python3 dispatch_book.py --help
```

Data is stored in `dispatch_book.json` next to the script (override with
`--db PATH`).

## Example

```console
$ python3 dispatch_book.py carrier add FASTFREIGHT
added carrier FASTFREIGHT

$ python3 dispatch_book.py carrier add SLUGLINE
added carrier SLUGLINE

$ python3 dispatch_book.py add FASTFREIGHT DSP-101 "ACME PUNE" 2026-09-01 2026-09-08 --goods "pump assemblies"
recorded dispatch DSP-101 via FASTFREIGHT to ACME PUNE: pickup scheduled 2026-09-01, delivery promised 2026-09-08

$ python3 dispatch_book.py add SLUGLINE DSP-102 "BETA NAGPUR" 2026-09-02 2026-09-09 --goods "castings"
recorded dispatch DSP-102 via SLUGLINE to BETA NAGPUR: pickup scheduled 2026-09-02, delivery promised 2026-09-09

$ python3 dispatch_book.py pickup DSP-101 2026-09-01
recorded pickup of DSP-101 on 2026-09-01 (on time)

$ python3 dispatch_book.py deliver DSP-101 2026-09-08
recorded delivery of DSP-101 on 2026-09-08 (on time)

$ python3 dispatch_book.py pickup DSP-102 2026-09-06
recorded pickup of DSP-102 on 2026-09-06 (4 days after schedule)

$ python3 dispatch_book.py deliver DSP-102 2026-09-15 --damaged --note "two crates dented"
recorded delivery of DSP-102 on 2026-09-15 (6 days past promise, DAMAGED (two crates dented))

$ python3 dispatch_book.py carriers
carrier                dispatches pickup on-time   avg late delivery on-time   avg late  damaged
FASTFREIGHT                     1           100%          0           100%          0        0
SLUGLINE                        1             0%          4             0%          6        1

$ python3 dispatch_book.py carrier show SLUGLINE --as-of 2026-11-22
SLUGLINE
  DSP-102  to BETA NAGPUR         pickup 2026-09-02  promise 2026-09-09  delivered 2026-09-15, 6 late, DAMAGED

$ python3 dispatch_book.py summary --as-of 2026-11-22
carriers: 2, dispatches: 2
  awaiting pickup: 0 (0 past schedule)
  in transit: 0 (0 past promise)
  delivered: 2 (1 damaged)
```

(Every line above is real output from a run of this script, with dates
chosen so the math is easy to check.)

## What else is out there (as of 2026-11-22)

Full transport management systems exist and do much more - Fleetx and
LocoNav, for example, offer GPS-tracked fleets, e-POD, freight matching
and analytics. This repo does not claim to be the only tool in this
space, and it is not a TMS. Its scope is deliberately smaller: an
offline, zero-install accountability record of scheduled vs actual
pickups and deliveries per carrier, in a file you fully own, for
manufacturers who book transport ad hoc and today keep this history in
their head or in chat threads. If you run a fleet or need live tracking,
use a TMS.

## Limits

- No live tracking: it records dates you enter; it does not track trucks,
  send alerts or integrate with carriers.
- "On time" compares the dates you record against the dates you
  scheduled. Time-of-day, weekends and holidays are not modeled.
- Statistics describe only what you recorded; they are not proof about
  dispatches you never entered.
- Averages round to whole days (half days round to the nearest even).
- Single user, single file. No multi-user access, no sync.
- Durability: saves are atomic (write to a temp file, then replace), which
  protects against interruption mid-save, not against deleting the file.
  Back up the JSON file like any other business record.

## Tests

```sh
python3 -m unittest test_dispatch_book -v
```

36 tests cover date parsing, carrier and dispatch validation, the
scheduled -> picked -> delivered lifecycle (skip-steps and double events
rejected), on-time/delay/overdue math, per-carrier statistics, pending
and in-transit queues, CSV export, atomic save/load roundtrip, and the
CLI end to end. CI runs the suite on Ubuntu and Windows against Python
3.9 and 3.13.

## License

MIT. See [LICENSE](LICENSE).
