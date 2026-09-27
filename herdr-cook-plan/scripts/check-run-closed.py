#!/usr/bin/env python3
"""Verify a herdr-cook-plan run root is closed before `run-completed` is appended.

Usage: check-run-closed.py <run-root> [<guard config outside the run root> ...]

Parses the files instead of matching text, so compact JSON, a `"commit": null` or a
moved ledger cannot pass by accident. Prints every failure and exits 1, or prints OK.
It checks the run's records and disposal only; it cannot tell whether a phase was really
accepted, which stays the coordinator's own verification.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

KEPT = {".gitignore", "checkpoint.md", "implementation-summary.md", "ledger.jsonl"}
TYPES = {"run-created", "lease-claimed", "answer", "phase-accepted", "run-completed", "run-abandoned"}
SHA = re.compile(r"^[0-9a-f]{7,40}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Fields each ledger type must carry as non-empty strings (recovery.md, Lease and ledger).
REQUIRED = {
    "run-created": ("owner", "plan"),
    "lease-claimed": ("owner", "previousOwner"),
    "answer": ("question_id", "file", "decision", "decided_by"),
    "phase-accepted": ("phase", "attempt", "agent", "pane"),
    "run-completed": ("owner",),
    "run-abandoned": ("owner", "reason"),
}
PHASE = re.compile(r"^p[0-9]{2}$")
ATTEMPT = re.compile(r"^[0-9]{2}$")


def nonempty(value):
    return isinstance(value, str) and value.strip() != ""


def check_config(path, failures):
    try:
        lifecycle = json.loads(path.read_text(encoding="utf-8")).get("lifecycle", "active")
    except (OSError, ValueError, AttributeError) as error:
        failures.append(f"{path}: unreadable guard config ({error})")
        return
    if lifecycle not in ("completed", "cancelled"):
        failures.append(f"{path}: guard config lifecycle is {lifecycle!r}, not completed or cancelled")


def check_ledger(path, run_id, failures):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        failures.append(f"ledger.jsonl: {error}")
        return
    entries = []
    for number, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError("not an object")
        except ValueError as error:
            failures.append(f"ledger.jsonl:{number}: not a JSON object ({error})")
            continue
        entries.append((number, entry))
    if not entries:
        failures.append("ledger.jsonl: no entries")
        return
    previous = None
    accepted = {}
    for number, entry in entries:
        where = f"ledger.jsonl:{number}"
        kind = entry.get("type")
        if kind not in TYPES:
            failures.append(f"{where}: unknown type {kind!r}")
        if entry.get("runId") != run_id:
            failures.append(f"{where}: runId {entry.get('runId')!r} is not {run_id!r}")
        if not isinstance(entry.get("epoch"), int) or isinstance(entry.get("epoch"), bool):
            failures.append(f"{where}: epoch is not an integer")
        ts = entry.get("ts")
        try:
            stamp = datetime.strptime(ts, TS_FORMAT) if isinstance(ts, str) and TS.match(ts) else None
        except ValueError:
            stamp = None
        if stamp is None:
            failures.append(f"{where}: ts {ts!r} is not a valid YYYY-MM-DDTHH:MM:SSZ time")
        else:
            if previous and stamp < previous:
                failures.append(f"{where}: ts goes backwards")
            previous = stamp
        missing = [field for field in REQUIRED.get(kind, ()) if not nonempty(entry.get(field))]
        if missing:
            failures.append(f"{where}: {kind} is missing {', '.join(missing)}")
        if kind == "run-created" and "flags" not in entry:
            failures.append(f"{where}: run-created is missing flags")
        if kind == "phase-accepted":
            commit, evidence = entry.get("commit"), entry.get("evidence")
            if not (isinstance(commit, str) and SHA.match(commit)) and not nonempty(evidence):
                failures.append(f"{where}: phase-accepted without a commit SHA or evidence pointer")
            phase = entry.get("phase")
            if nonempty(phase) and not PHASE.match(phase):
                failures.append(f"{where}: phase {phase!r} is not a phase key like p02")
            if nonempty(entry.get("attempt")) and not ATTEMPT.match(entry["attempt"]):
                failures.append(f"{where}: attempt {entry['attempt']!r} is not two digits")
            if phase in accepted:
                failures.append(f"{where}: phase {phase!r} already accepted at line {accepted[phase]}")
            accepted[phase] = number
    if entries[0][1].get("type") != "run-created":
        failures.append("ledger.jsonl: first entry is not run-created")
    if not accepted:
        failures.append("ledger.jsonl: no phase-accepted entry")
    kinds = [entry.get("type") for _, entry in entries]
    if "run-abandoned" in kinds:
        failures.append("ledger.jsonl: an abandoned run is not closed through completion")
    if "run-completed" in kinds:
        tail = kinds[kinds.index("run-completed") + 1:]
        if kinds.count("run-completed") > 1 or any(kind != "lease-claimed" for kind in tail):
            failures.append("ledger.jsonl: run-completed must be recorded once, after every other event")


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    root = Path(argv[1])
    failures = []
    if not root.is_dir():
        print(f"FAIL {root}: not a directory")
        return 1
    names = {entry.name for entry in root.iterdir()}
    for extra in sorted(names - KEPT):
        failures.append(f"{extra}: left in the run root (only {', '.join(sorted(KEPT))} stay)")
    for missing in sorted(KEPT - names):
        failures.append(f"{missing}: missing from the run root")
    for config in sorted(root.rglob("*.json")):
        check_config(config, failures)
    for extra in argv[2:]:
        path = Path(extra)
        if path.exists():
            check_config(path, failures)
    if (root / "ledger.jsonl").is_file():
        check_ledger(root / "ledger.jsonl", root.name, failures)
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        return 1
    print(f"OK {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
