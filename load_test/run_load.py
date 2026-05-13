"""CP4 load harness.

One Python file, stdlib only. Three modes:

  python load_test/run_load.py --mode constant --rate 5 --duration 30 --label baseline
  python load_test/run_load.py --mode step    --rates 2,4,6,8,10 --duration 20
  python load_test/run_load.py --mode spike   --base-rate 2 --spike-rate 12 \
                               --baseline-s 20 --spike-s 30 --recovery-s 20

Outputs:

  - prints a summary table per stage (rate, total, OK, errors, p50, p95, p99, mean s)
  - writes per-request rows to load_test/results/<label>.csv
                                (columns: t,stage,rate_target,status,latency_s)

The harness scheduler dispatches `rate` requests/second to a thread pool. Each
worker POSTs a clean-order payload to /checkout and records the wall-clock
latency. Errors (HTTP != 200 or exception) are counted but do not stop the
run.
"""

import argparse
import csv
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

ORCH = os.getenv("CP4_ORCHESTRATOR_URL", "http://127.0.0.1:8081/checkout")

# Five seeded books in books_database/src/app.py; rotate so per-title lock
# contention reflects real-ish demand and we don't deplete one title.
TITLES = [
    "Book A",
    "Book B",
    "Book C",
    "Distributed Systems Basics",
    "Designing Data-Intensive Applications",
]

DEFAULT_RESULTS = Path("load_test/results")


def _payload(idx: int) -> bytes:
    title = TITLES[idx % len(TITLES)]
    return json.dumps(
        {
            "user": {
                "name": f"Load User {idx}",
                "contact": f"u{idx}@load.test",
                "creditCard": {
                    "number": "4111111111111111",
                    "expirationDate": "12/30",
                    "cvv": "123",
                },
            },
            "items": [{"name": title, "quantity": 1}],
            "shippingMethod": "Standard",
            "termsAndConditionsAccepted": True,
        }
    ).encode("utf-8")


@dataclass
class Sample:
    t: float
    stage: str
    rate_target: float
    status: int
    latency_s: float


@dataclass
class StageStats:
    label: str
    rate: float
    samples: List[Sample] = field(default_factory=list)

    def add(self, s: Sample) -> None:
        self.samples.append(s)


def _q(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[idx]


def _one_request(idx: int) -> "tuple[int, float]":
    body = _payload(idx)
    req = urllib.request.Request(
        ORCH,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            resp.read()
            return resp.status, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0
    except Exception:
        return 0, time.time() - t0


def _run_stage(
    label: str,
    target_rps: float,
    duration_s: float,
    stats: StageStats,
    pool: ThreadPoolExecutor,
    counter: dict,
) -> None:
    """Open-loop scheduler: aim for `target_rps` requests per second.

    Open-loop matters: a closed-loop "fire next when previous returned"
    approach would underestimate throughput when each request is slow, because
    workers would be sitting on RPCs instead of issuing new ones. Open-loop
    fires on the wall clock, so the load is real even if the system back-
    pressures.
    """
    interval = 1.0 / max(target_rps, 0.001)
    end = time.time() + duration_s
    next_fire = time.time()

    def _worker(i: int):
        status, latency = _one_request(i)
        stats.add(
            Sample(
                t=time.time(),
                stage=label,
                rate_target=target_rps,
                status=status,
                latency_s=latency,
            )
        )

    while time.time() < end:
        now = time.time()
        if now < next_fire:
            time.sleep(min(0.005, next_fire - now))
            continue
        pool.submit(_worker, counter["idx"])
        counter["idx"] += 1
        next_fire += interval


def run(args: argparse.Namespace) -> None:
    DEFAULT_RESULTS.mkdir(parents=True, exist_ok=True)
    label = args.label or args.mode
    csv_path = DEFAULT_RESULTS / f"{label}.csv"

    counter = {"idx": 0}
    all_stages: List[StageStats] = []
    # Pool size needs to keep up with target RPS x worst-case latency. /checkout
    # under stress can take ~5s; 64 workers is enough for ~12 RPS at 5s/req.
    pool_size = max(64, int(args.max_workers))
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        if args.mode == "constant":
            stage = StageStats(label=f"constant@{args.rate}", rate=args.rate)
            all_stages.append(stage)
            _run_stage(stage.label, args.rate, args.duration, stage, pool, counter)
        elif args.mode == "step":
            rates = [float(r) for r in args.rates.split(",") if r]
            for r in rates:
                stage = StageStats(label=f"step@{r}", rate=r)
                all_stages.append(stage)
                _run_stage(stage.label, r, args.duration, stage, pool, counter)
        elif args.mode == "spike":
            for sublabel, rate, dur in [
                ("baseline", args.base_rate, args.baseline_s),
                ("spike", args.spike_rate, args.spike_s),
                ("recovery", args.base_rate, args.recovery_s),
            ]:
                stage = StageStats(label=sublabel, rate=rate)
                all_stages.append(stage)
                _run_stage(stage.label, rate, dur, stage, pool, counter)
        else:
            raise SystemExit(f"unknown mode: {args.mode}")

        # Drain: give in-flight requests a moment to finish so we don't lose
        # their samples to the pool shutdown.
        pool.shutdown(wait=True)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t", "stage", "rate_target", "status", "latency_s"])
        for st in all_stages:
            for s in st.samples:
                w.writerow([f"{s.t:.6f}", s.stage, f"{s.rate_target:.2f}", s.status, f"{s.latency_s:.6f}"])
    print(f"\nCSV: {csv_path}")
    print("-" * 130)
    print(f"{'stage':>14}  {'config':<40} n / ok / err   latency p50 / p95 / p99 / mean   observed_rps")
    print("-" * 130)
    for st in all_stages:
        print(_pretty(st))


def _pretty(st: StageStats) -> str:
    if not st.samples:
        return f"{st.label:>14}  rate={st.rate:>5.2f}  no samples"
    lats = [s.latency_s for s in st.samples]
    ok = sum(1 for s in st.samples if 200 <= s.status < 300)
    n = len(st.samples)
    duration = max(0.001, max(s.t for s in st.samples) - min(s.t for s in st.samples))
    return (
        f"{st.label:>14}  rate_target={st.rate:>5.2f}  "
        f"n={n:>5} ok={ok:>5} err={n-ok:>4}  "
        f"p50={statistics.median(lats):.3f}s p95={_q(lats, 0.95):.3f}s "
        f"p99={_q(lats, 0.99):.3f}s mean={statistics.fmean(lats):.3f}s  "
        f"observed_rps={n/duration:.2f}"
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CP4 load harness")
    p.add_argument("--mode", choices=["constant", "step", "spike"], required=True)
    p.add_argument("--label", default="", help="filename label; default = mode")
    p.add_argument("--duration", type=float, default=20.0, help="seconds per stage")
    p.add_argument("--rate", type=float, default=4.0, help="constant mode rate (RPS)")
    p.add_argument("--rates", default="2,4,6,8,10", help="step mode rates (CSV)")
    p.add_argument("--base-rate", type=float, default=2.0, help="spike mode baseline RPS")
    p.add_argument("--spike-rate", type=float, default=12.0, help="spike mode peak RPS")
    p.add_argument("--baseline-s", type=float, default=20.0)
    p.add_argument("--spike-s", type=float, default=30.0)
    p.add_argument("--recovery-s", type=float, default=20.0)
    p.add_argument("--max-workers", type=int, default=128)
    p.add_argument("--seed", type=int, default=0)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.seed:
        random.seed(args.seed)
    run(args)
