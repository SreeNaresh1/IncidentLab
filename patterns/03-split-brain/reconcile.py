#!/usr/bin/env python3
"""
Reconciliation for Pattern 03 -- run against whatever divergent data BREAK
left behind. FIX deliberately preserves it (see fix.sh and the pattern
README) specifically so this script has something real to act on, rather
than reconciliation being an untested code path.

Rule, in order, per key:
  1. If both sides agree (same epoch, same value), no-op.
  2. If both sides claim the SAME real (>0) epoch with DIFFERENT values,
     that's an anomaly epoch alone can't resolve (shouldn't be possible
     given the witness only ever grants one node a given epoch) -- flag
     it and touch nothing, rather than silently picking one.
  3. Otherwise, whichever side holds the higher epoch wins, provided that
     epoch is a real one (> 0) -- pushed to the other side.
  4. If neither side has a genuinely fenced (epoch > 0) copy of this key
     at all -- whether because both sides disagree at epoch 0, only one
     side ever wrote it unfenced, or both independently hold different
     unfenced values -- the key is discarded on both sides, not
     arbitrarily kept. The absence of a competing value doesn't make an
     unfenced value any more trustworthy: every BREAK-era naive write
     looks exactly like this, and none of it was ever backed by real
     quorum. Untrusted data can't be reconciled into something
     trustworthy; it can only be recognized as loss and removed, so the
     client can re-submit it properly under the new regime.

Stdlib only, deliberately -- this runs on the host alongside the other
stage scripts, which require nothing beyond python3's standard library.

Usage: reconcile.py <node1_url> <node2_url> [--apply] [--output FILE]
Without --apply, prints the plan without pushing any writes (dry run).
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def _post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def reconcile(node1_url, node2_url, apply_changes):
    dump1 = _get(f"{node1_url}/kv/_dump")
    dump2 = _get(f"{node2_url}/kv/_dump")

    all_keys = sorted(set(dump1) | set(dump2))
    report = {
        "keys_compared": len(all_keys),
        "pushed": [],
        "discarded": [],
        "already_consistent": [],
    }

    for key in all_keys:
        v1 = dump1.get(key)
        v2 = dump2.get(key)
        e1 = v1["epoch"] if v1 else None
        e2 = v2["epoch"] if v2 else None

        if e1 is not None and e2 is not None and e1 == e2:
            if e1 > 0 and v1["value"] == v2["value"]:
                report["already_consistent"].append({"key": key, "epoch": e1})
            elif e1 > 0:
                # Same real epoch, different values. Shouldn't be possible
                # given the witness only ever grants one node a given
                # epoch -- if it happens anyway, epoch alone can't decide
                # which is right, so don't guess. Flag it, touch nothing.
                report["discarded"].append({
                    "key": key,
                    "reason": f"anomaly: both sides claim epoch {e1} with different values "
                              f"-- cannot resolve by epoch alone, needs manual review",
                })
            else:
                report["discarded"].append(
                    {"key": key, "reason": "tied at epoch 0 -- unfenced on both sides, unrecoverable"}
                )
                if apply_changes:
                    if v1 is not None:
                        _post(f"{node1_url}/admin/force-write", {"key": key, "delete": True})
                    if v2 is not None:
                        _post(f"{node2_url}/admin/force-write", {"key": key, "delete": True})
            continue

        candidates = [e for e in (e1, e2) if e is not None]
        best_epoch = max(candidates) if candidates else None

        if best_epoch is None or best_epoch <= 0:
            # No side has a genuinely fenced copy of this key -- nothing
            # trustworthy to keep, whether that's because only one side
            # ever wrote it unfenced or both sides hold different
            # unfenced values. Absence of a competing value doesn't make
            # an unfenced value any more authoritative.
            report["discarded"].append(
                {"key": key, "reason": "no fenced (epoch > 0) copy exists on either side"}
            )
            if apply_changes:
                if v1 is not None:
                    _post(f"{node1_url}/admin/force-write", {"key": key, "delete": True})
                if v2 is not None:
                    _post(f"{node2_url}/admin/force-write", {"key": key, "delete": True})
        elif e1 == best_epoch:
            report["pushed"].append({"key": key, "winner": "node-1", "epoch": e1, "loser": "node-2"})
            if apply_changes:
                _post(f"{node2_url}/admin/force-write", {"key": key, "value": v1["value"], "epoch": e1})
        else:
            report["pushed"].append({"key": key, "winner": "node-2", "epoch": e2, "loser": "node-1"})
            if apply_changes:
                _post(f"{node1_url}/admin/force-write", {"key": key, "value": v2["value"], "epoch": e2})

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("node1_url")
    parser.add_argument("node2_url")
    parser.add_argument("--apply", action="store_true", help="Actually push corrective writes (default: dry run)")
    parser.add_argument("--output", help="Write the JSON report to this path")
    args = parser.parse_args()

    try:
        report = reconcile(args.node1_url, args.node2_url, args.apply)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"Reconciliation failed: {exc}", file=sys.stderr)
        sys.exit(2)

    print(json.dumps(report, indent=2))
    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)

    sys.exit(0)


if __name__ == "__main__":
    main()
