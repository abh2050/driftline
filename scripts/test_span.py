"""Send one test span through otel-collector and wait for it in Log Analytics.

Phase 2 acceptance: proves the path service -> collector -> Azure Monitor works.
The collector must be reachable from this machine: set OTEL_ALLOWED_IPS in the azd env so
its ingress is external and IP-restricted, then run azd provision.

Usage: uv run python scripts/test_span.py [--timeout 600]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta

from azure.identity import AzureCliCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus

from driftline_common.telemetry import setup_telemetry
from scripts.smoke import azd_env


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    env = azd_env()
    endpoint = f"https://{env['OTEL_COLLECTOR_FQDN']}"
    tel = setup_telemetry("smoke-test", endpoint=endpoint)
    tracer = tel.tracer_provider.get_tracer("driftline.smoke")
    # driftline.incident_id makes the tail sampler keep the trace.
    with tracer.start_as_current_span("smoke.test_span") as span:
        span.set_attribute("driftline.incident_id", "smoke")
        trace_id = format(span.get_span_context().trace_id, "032x")
    tel.shutdown()
    print(f"sent span trace_id={trace_id} to {endpoint}; waiting for Log Analytics...")

    client = LogsQueryClient(AzureCliCredential())
    query = f'search "{trace_id}" | project $table, TimeGenerated | take 5'
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        res = client.query_workspace(
            env["LOG_ANALYTICS_WORKSPACE_ID"], query, timespan=timedelta(hours=1)
        )
        if res.status == LogsQueryStatus.SUCCESS and res.tables and res.tables[0].rows:
            for row in res.tables[0].rows:
                print(f"found in table {row[0]} at {row[1]}")
            print("test span: OK")
            return 0
        time.sleep(20)
    print("test span: NOT FOUND within timeout")
    return 1


if __name__ == "__main__":
    sys.exit(main())
