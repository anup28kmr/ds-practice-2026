"""Snapshot a fixed set of Prometheus queries from the OTEL-LGTM container.

Used by the load suite to capture 2PC counters, p95 latency, and DB write
totals at the end of a load run, so docs/checkpoint-4-evaluation.md can
cite real numbers.
"""

import base64
import json
import urllib.parse
import urllib.request

AUTH = "Basic " + base64.b64encode(b"admin:admin").decode()
BASE = "http://127.0.0.1:3000/api/datasources/proxy/uid/prometheus/api/v1/query"


def q(query: str):
    url = BASE + "?query=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"Authorization": AUTH})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


QUERIES = [
    ("2pc_total by outcome", "sum by (outcome) (twopc_total)"),
    ("db_writes_total by path", "sum by (path) (db_writes_total)"),
    ("payment_total by phase,outcome", "sum by (phase, outcome) (payment_total)"),
    ("checkout p95 (5m)", "histogram_quantile(0.95, sum by (le) (rate(checkout_latency_seconds_bucket[5m])))"),
    ("checkout p50 (5m)", "histogram_quantile(0.50, sum by (le) (rate(checkout_latency_seconds_bucket[5m])))"),
    ("2pc p95 (5m)", "histogram_quantile(0.95, sum by (le, outcome) (rate(twopc_latency_seconds_bucket[5m])))"),
    ("inflight_2pc_attempts", "inflight_2pc_attempts"),
    ("db_pending_orders", "db_pending_orders"),
    ("db_kv_store_keys", "db_kv_store_keys"),
    ("executor_is_primary", "executor_is_primary"),
]


def fmt_value(v):
    try:
        return f"{float(v):.4f}"
    except (ValueError, TypeError):
        return str(v)


def main():
    for title, expr in QUERIES:
        print(f"== {title}")
        try:
            r = q(expr)
        except Exception as e:
            print(f"  ERROR: {e!r}")
            continue
        rs = r.get("data", {}).get("result", [])
        if not rs:
            print("  (no series)")
        for x in rs:
            labels = ",".join(
                f"{k}={v}" for k, v in x["metric"].items()
                if k not in ("__name__", "instance", "job", "service_instance_id")
            )
            value = fmt_value(x["value"][1])
            print(f"  {labels:<60} = {value}")
        print()


if __name__ == "__main__":
    main()
