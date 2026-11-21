"""Save and replay decision snapshots without making model calls."""

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from .engine import evaluate, validate_policy


_MAX_SNAPSHOT_BYTES = 1024 * 1024


def _directory(storage_dir):
    return Path(storage_dir) / "regressions"


def _invalid_constant(value):
    raise ValueError("Regression file contains a non-finite JSON number.")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Regression file contains an overflowing JSON number.")
    return number


def _load(path):
    # A corrupt local file must not make the whole saved suite unavailable.
    with path.open("rb") as stream:
        content = stream.read(_MAX_SNAPSHOT_BYTES + 1)
    if len(content) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("Regression file exceeds 1 MiB.")
    try:
        record = json.loads(content.decode("utf-8"), parse_constant=_invalid_constant,
                            parse_float=_finite_float)
    except RecursionError:
        raise ValueError("Regression JSON nesting is too deep.") from None
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise ValueError("Unsupported regression schema.")
    if not isinstance(record.get("case"), dict) or not isinstance(record.get("semantic"), dict):
        raise ValueError("Missing case or semantic snapshot.")
    if record.get("expected_outcome") not in ("allow", "review", "deny"):
        raise ValueError("Invalid expected outcome.")
    record["policy"] = validate_policy(record.get("policy"))
    return record


def list_regressions(storage_dir):
    records = []
    for path in sorted(_directory(storage_dir).glob("*.json")):
        try:
            record = _load(path)
            records.append({"id": path.stem, "case_id": record["case"].get("id"),
                            "title": record["case"].get("title"),
                            "policy": record["policy"], "expected_outcome": record["expected_outcome"]})
        except (OSError, ValueError, TypeError):
            records.append({"id": path.stem, "error": "Invalid regression file."})
    return records


def save_regression(storage_dir, case, policy, semantic, expected_outcome):
    if expected_outcome not in ("allow", "review", "deny"):
        raise ValueError("expected_outcome must be allow, review, or deny.")
    policy = validate_policy(policy)
    decision = evaluate(case, policy, semantic)
    record = {"schema_version": 1, "case": case, "policy": policy,
              "semantic": semantic, "policy_hash": decision["policy_hash"],
              "expected_outcome": expected_outcome}
    content = json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    if len((content + "\n").encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("Regression file exceeds 1 MiB.")
    identifier = hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]
    directory = _directory(storage_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (identifier + ".json")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=str(directory),
                                         suffix=".tmp", delete=False, newline="\n") as stream:
            temporary = Path(stream.name)
            stream.write(content + "\n")
        os.replace(str(temporary), str(destination))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return {"saved": True, "id": identifier, "count": len(list_regressions(storage_dir))}


def run_regressions(storage_dir, policy=None):
    """Check saved expectations, optionally under a new policy, without writes.

    The default replays each snapshot's own policy. An explicit policy keeps
    the saved facts, semantic observation and expectation fixed while checking
    that policy instead. These are behavioral checks, not model evaluations.
    """
    override = validate_policy(policy) if policy is not None else None
    results = []
    for path in sorted(_directory(storage_dir).glob("*.json")):
        try:
            record = _load(path)
            evaluated_policy = override if override is not None else record["policy"]
            decision = evaluate(record["case"], evaluated_policy, record["semantic"])
            passed = decision["outcome"] == record["expected_outcome"]
            results.append({"id": path.stem, "case_id": record["case"].get("id"),
                            "title": record["case"].get("title"),
                            "saved_policy": record["policy"], "evaluated_policy": evaluated_policy,
                            "passed": passed, "expected": record["expected_outcome"],
                            "actual": decision["outcome"], "reason": decision["reason"]})
        except RecursionError:
            results.append({"id": path.stem, "passed": False,
                            "error": "Regression evidence nesting is too deep."})
        except (OSError, ValueError, TypeError) as error:
            results.append({"id": path.stem, "passed": False, "error": str(error)})
    passed = sum(item["passed"] for item in results)
    report = {"total": len(results), "passed": passed, "failed": len(results) - passed,
              "results": results, "mode": "draft" if override is not None else "saved"}
    if override is not None:
        report["policy"] = override
    return report
