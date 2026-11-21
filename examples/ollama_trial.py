"""Run four local model calls and inspect six controlled refund scenarios.

Run from the repository root: python -m examples.ollama_trial
This is an integration smoke test, not a model accuracy or calibration benchmark.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib import request

from ontology_jev import evaluate
from ontology_jev.fixtures import BASELINE_POLICY, DRAFT_POLICY, load_cases
from ontology_jev.providers import OllamaProvider, ProviderError, _NoRedirect


MESSAGES = {
    "explicit": "Please refund my order. I would like my payment returned.",
    "ambiguous": "This order was disappointing. What can you do about it?",
    "negative": "I do not want a refund. Please keep the order and just tell me when the replacement will arrive.",
    "explicit_zh": "请帮我把这笔订单退款，我希望退回原支付账户。",
}


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    timestamp = datetime.now(timezone.utc)
    output = args.output or Path(".ontologyjev") / "trials" / (timestamp.strftime("%Y%m%dT%H%M%S%fZ") + "-ollama.json")
    # Never accidentally overwrite an earlier trial's model evidence.
    if output.exists():
        parser.error("Output already exists; choose a new --output path.")
    provider = OllamaProvider(model=args.model, base_url=args.base_url, timeout=args.timeout)
    source_root = Path(__file__).resolve().parents[1]
    report = {
        "created_at": timestamp.isoformat(),
        "kind": "local_integration_smoke",
        "evidence_note": "Live Ollama outputs over constructed cases. Scores are generated model estimates, not Jev outputs or calibrated probabilities. This does not measure real-world accuracy. Actions are proposals only.",
        "model": args.model, "base_url": args.base_url,
        "baseline_policy": BASELINE_POLICY, "draft_policy": DRAFT_POLICY,
        "source_sha256": {
            name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
            for name in ("ontology_jev/providers.py", "ontology_jev/engine.py", "examples/ollama_trial.py")
        },
        "observations": {}, "scenarios": [], "complete": False,
    }
    # This URL has already passed the provider's loopback validation.
    try:
        opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
        with opener.open(args.base_url.rstrip("/") + "/api/version", timeout=10) as response:
            report["ollama_version"] = json.loads(response.read(65536).decode("utf-8"))
        with opener.open(args.base_url.rstrip("/") + "/api/tags", timeout=10) as response:
            models = json.loads(response.read(1024 * 1024).decode("utf-8"))
        report["installed_model"] = next((model for model in models.get("models", []) if model.get("name") == args.model), None)
    except (OSError, ValueError) as exc:
        report["ollama_version"] = {"unavailable": type(exc).__name__}
    save_report(output, report)
    start = time.perf_counter()
    for label, message in MESSAGES.items():
        print("Calling local model: " + label, flush=True)
        observation = {"message": message}
        try:
            observation["semantic"] = provider.judge({"message": message})
            print("  score={:.4f}".format(observation["semantic"]["refund_request"]), flush=True)
        except ProviderError as exc:
            observation["error"] = str(exc)
            print("  error=" + str(exc), flush=True)
        report["observations"][label] = observation
        save_report(output, report)

    cases = {case["id"]: case for case in load_cases()}
    scenarios = [
        ("window_change", "case-001", "explicit"),
        ("wrong_owner", "case-004", "explicit"),
        ("already_refunded", "case-005", "explicit"),
        ("ambiguous_message", "case-009", "ambiguous"),
        ("not_requesting_refund", "case-002", "negative"),
        ("chinese_refund", "case-002", "explicit_zh"),
    ]
    for label, case_id, observation_key in scenarios:
        case = copy.deepcopy(cases[case_id])
        # Remove fixture scores so no synthetic value can stand in for a failed call.
        case.pop("semantic", None)
        case["message"] = MESSAGES[observation_key]
        observation = report["observations"][observation_key]
        result = {"name": label, "case": case, "observation_key": observation_key}
        if "error" in observation:
            result["error"] = observation["error"]
        else:
            semantic = observation["semantic"]
            result["baseline"] = evaluate(case, BASELINE_POLICY, semantic)
            result["draft"] = evaluate(case, DRAFT_POLICY, semantic)
            print("{}: {} -> {} | {}".format(label, result["baseline"]["outcome"], result["draft"]["outcome"], result["draft"]["reason"]), flush=True)
        report["scenarios"].append(result)
    report["elapsed_seconds"] = time.perf_counter() - start
    report["failed_calls"] = sum("error" in item for item in report["observations"].values())
    report["successful_calls"] = len(report["observations"]) - report["failed_calls"]
    report["complete"] = report["failed_calls"] == 0
    save_report(output, report)
    print("Report: " + str(output.resolve()), flush=True)
    return int(not report["complete"])


if __name__ == "__main__":
    raise SystemExit(main())
