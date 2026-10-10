"""driftline-eval: run Driftline evaluation layers (spec section 7).

uv run driftline-eval layer1 --label baseline            # live, uploads to Foundry
uv run driftline-eval layer1 --label baseline --offline  # recompute from fixtures (CI)
uv run driftline-eval layer1 --fault F3 --label F3       # a fault's config
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from evals.layer1.metrics import gate


def _layer1(args: argparse.Namespace) -> int:
    from evals.layer1.run import REPORTS, run_live, run_offline

    if args.offline:
        metrics = run_offline(args.label)
        stored = sorted(REPORTS.glob(f"*-layer1-{args.label}.json"))
        print(json.dumps(metrics, indent=2))
        if stored:
            expected = json.loads(stored[-1].read_text())["metrics"]
            if expected != metrics:
                print(f"offline metrics differ from {stored[-1].name}", file=sys.stderr)
                return 1
            print(f"matches {stored[-1].name}")
        return 0
    if args.rejudge:
        from evals.layer1.run import run_rejudge

        print(json.dumps(run_rejudge(args.label, upload=not args.no_upload), indent=2))
        return 0
    overrides: dict[str, object] = {}
    if args.fault:
        from patient_rag.config import load_fault

        overrides.update(load_fault(args.fault).overrides)
    for kv in args.set or []:
        k, v = kv.split("=", 1)
        overrides[k] = yaml.safe_load(v)
    metrics = run_live(
        args.label,
        overrides,
        source_version=args.source_version,
        limit=args.limit,
        concurrency=args.concurrency,
        upload=not args.no_upload,
    )
    print(json.dumps(metrics, indent=2))
    return 0


def _layer2a(args: argparse.Namespace) -> int:
    import asyncio

    from driftline_common.config import load_router_config
    from evals.layer2a_router.run import report, run

    clients = args.clients.split(",") if args.clients else None
    if args.offline:
        cfg = load_router_config()
        data = report(clients or list(cfg.clients), args.sample, cfg.escalation_threshold)
    else:
        data = asyncio.run(run(clients, args.sample))
    for c, types in data["clients"].items():
        print(c, {t: m["accuracy"] for t, m in types.items()})
    return 0


def _gate(args: argparse.Namespace) -> int:
    from evals.layer1.run import run_offline

    gates = yaml.safe_load((Path(__file__).parent / "gates.yaml").read_text())
    result = gate(run_offline(args.baseline), run_offline(args.candidate), args.metric, gates)
    print(
        json.dumps(
            {
                "passed": result.passed,
                "recovered": result.recovered,
                "regressions": result.regressions,
            },
            indent=2,
        )
    )
    return 0 if result.passed else 1


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="driftline-eval",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="layer", required=True)
    l1 = sub.add_parser("layer1", help="RAG quality on the golden set")
    l1.add_argument("--label", default="baseline")
    l1.add_argument("--offline", action="store_true")
    l1.add_argument("--rejudge", action="store_true", help="re-score a stored fixture")
    l1.add_argument("--fault")
    l1.add_argument("--set", action="append", help="config override key=value")
    l1.add_argument("--source-version", type=int, default=1)
    l1.add_argument("--limit", type=int)
    l1.add_argument("--concurrency", type=int, default=2)
    l1.add_argument("--no-upload", action="store_true")
    l1.set_defaults(fn=_layer1)
    l2a = sub.add_parser("layer2a", help="router benchmark over the routing dataset")
    l2a.add_argument("--clients", help="comma-separated client names (default: all)")
    l2a.add_argument("--sample", type=int, help="first N real cases (quick runs)")
    l2a.add_argument("--offline", action="store_true", help="report from stored results")
    l2a.set_defaults(fn=_layer2a)
    g = sub.add_parser("gate", help="apply evals/gates.yaml to two layer 1 fixtures")
    g.add_argument("--baseline", default="baseline")
    g.add_argument("--candidate", required=True)
    g.add_argument("--metric", required=True, help="the degraded metric that must recover")
    g.set_defaults(fn=_gate)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
