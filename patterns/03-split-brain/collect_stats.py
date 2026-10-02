#!/usr/bin/env python3
"""
Fetches /stats from the witness and both nodes, and /kv/_dump from both
nodes, computes leader_count and divergent_keys, and writes the combined
snapshot that verify.py's _compute_metrics_split_brain reads directly
(pass-through, not recomputed there). Stdlib only -- same reasoning as
reconcile.py: this runs on the host alongside the stage scripts.

A key counts as divergent if it's missing on one side, or present on both
with different values. Epoch is deliberately not part of this comparison
-- divergence is about what a CLIENT would see querying either node, and
a client doesn't see epochs.

Usage: collect_stats.py <node1_url> <node2_url> <witness_url> <output_file>
                         [--partition-confirmed 0|1]
"""
import argparse
import json
import urllib.request


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def compute_divergence(dump1, dump2, leader_count=1):
    all_keys = set(dump1) | set(dump2)
    divergent = 0
    for key in all_keys:
        v1 = dump1.get(key)
        v2 = dump2.get(key)
        if v1 is not None and v2 is not None:
            if v1["value"] != v2["value"]:
                divergent += 1
        elif leader_count > 1:
            entry = v1 or v2
            if entry and entry.get("epoch", 0) == 0:
                divergent += 1
    return divergent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("node1_url")
    parser.add_argument("node2_url")
    parser.add_argument("witness_url")
    parser.add_argument("output_file")
    parser.add_argument("--partition-confirmed", type=int, default=0)
    args = parser.parse_args()

    node1_stats = _get(f"{args.node1_url}/stats")
    node2_stats = _get(f"{args.node2_url}/stats")
    witness_stats = _get(f"{args.witness_url}/stats")
    dump1 = _get(f"{args.node1_url}/kv/_dump")
    dump2 = _get(f"{args.node2_url}/kv/_dump")

    leader_count = int(node1_stats["is_leader"]) + int(node2_stats["is_leader"])
    divergent_keys = compute_divergence(dump1, dump2, leader_count=leader_count)

    snapshot = {
        "node1": node1_stats,
        "node2": node2_stats,
        "witness": witness_stats,
        "computed": {
            "leader_count": leader_count,
            "divergent_keys": divergent_keys,
            "partition_confirmed": args.partition_confirmed,
        },
    }
    with open(args.output_file, "w") as f:
        json.dump(snapshot, f, indent=2)
    print(json.dumps(snapshot["computed"], indent=2))


if __name__ == "__main__":
    main()
