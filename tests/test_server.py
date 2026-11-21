import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from unittest.mock import patch

from ontology_jev.engine import evaluate
from ontology_jev.providers import ProviderError
from ontology_jev.server import create_server
from ontology_jev.regressions import run_regressions


class WorkbenchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = create_server("127.0.0.1", 0, Path(self.directory.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%s" % self.server.server_port
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, path, data=None, headers=None):
        body = None if data is None else json.dumps(data).encode()
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = Request(self.url + path, data=body, headers=request_headers)
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            return response.status, json.loads(content) if content.startswith(b"{") else content

    def test_initial_workspace_and_policy_only_diff(self):
        status, initial = self.request("/api/workspace")
        self.assertEqual(status, 200)
        self.assertEqual(initial["summary"]["total"], 20)
        self.assertEqual(initial["summary"]["changed"], 5)
        self.assertEqual(initial["provider"]["mode"], "fixture")
        self.assertFalse(initial["provider"]["live"])
        status, reset = self.request("/api/compare", {"policy": initial["baseline_policy"]})
        self.assertEqual(status, 200)
        self.assertEqual(reset["summary"]["changed"], 0)
        for row in initial["rows"]:
            self.assertEqual(row["before"]["semantic"], row["after"]["semantic"])

    def test_workspace_and_compare_advertise_ollama_without_provider_calls(self):
        with patch("ontology_jev.server.JevProvider") as jev, \
                patch("ontology_jev.server.OllamaProvider", create=True) as ollama:
            status, initial = self.request("/api/workspace")
            self.assertEqual(status, 200)
            self.assertIn("ollama_model", initial["provider"])
            self.assertEqual(initial["provider"]["ollama_model"], "qwen3.5:9b")
            self.assertIn("jev_available", initial["provider"])
            self.assertEqual(self.request("/api/compare", {"policy": initial["candidate_policy"]})[0], 200)
            jev.assert_not_called()
            ollama.assert_not_called()

    def test_live_dispatches_once_and_reuses_observation_for_both_policies(self):
        _, initial = self.request("/api/workspace")
        fixture = initial["rows"][0]["case"]
        policy = initial["candidate_policy"]
        for provider in ("ollama", "jev", None):
            selected = "jev" if provider is None else provider
            observation = {"refund_request": 0.91, "source": selected,
                           "model": "actual-returned-model", "usage": {"input_tokens": 5}}
            payload = {"case_id": fixture["id"], "policy": policy}
            if provider is not None:
                payload["provider"] = provider
            with self.subTest(provider=provider), \
                    patch("ontology_jev.server.JevProvider") as jev, \
                    patch("ontology_jev.server.OllamaProvider", create=True) as ollama, \
                    patch("ontology_jev.server.evaluate", wraps=evaluate) as judge_policy:
                chosen, other = (ollama, jev) if selected == "ollama" else (jev, ollama)
                chosen.return_value.judge.return_value = observation
                other.return_value.judge.return_value = observation
                status, result = self.request("/api/live", payload)
                self.assertEqual(status, 200)
                self.assertEqual(set(result), {"case", "policy", "semantic", "decision", "baseline_decision"})
                chosen.assert_called_once_with()
                chosen.return_value.judge.assert_called_once()
                other.assert_not_called()
                self.assertNotIn("semantic", result["case"])
                self.assertEqual(result["case"], {key: value for key, value in fixture.items() if key != "semantic"})
                self.assertEqual(result["policy"], policy)
                self.assertEqual(result["semantic"], observation)
                self.assertEqual(result["baseline_decision"]["outcome"], "deny")
                self.assertEqual(result["decision"]["outcome"], "allow")
                self.assertEqual(judge_policy.call_count, 2)
                self.assertEqual({call.args[1]["version"] for call in judge_policy.call_args_list},
                                 {initial["baseline_policy"]["version"], policy["version"]})
                for call in judge_policy.call_args_list:
                    self.assertIs(call.args[2], observation)
                for key in ("decision", "baseline_decision"):
                    self.assertEqual(result[key]["semantic"]["model"], "actual-returned-model")
                    self.assertEqual(result[key]["semantic"]["source"], selected)
        self.assertEqual(self.request("/api/workspace")[1], initial)
        self.assertEqual(self.request("/api/regressions")[1], {"regressions": []})
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_live_message_and_model_overrides_preserve_fixtures_and_provenance(self):
        _, initial = self.request("/api/workspace")
        fixture = initial["rows"][0]["case"]
        message, model = "\u9000" * 4000, "m" * 120
        observation = {"refund_request": 0.93, "source": "ollama", "model": "actual:9b",
                       "score_kind": "generated_self_report", "calibrated": False,
                       "raw_request": {"model": model}, "raw_response": {"model": "actual:9b"},
                       "explanation": "The message requests a refund.", "elapsed_seconds": 0.4}
        with patch("ontology_jev.server.JevProvider") as jev, \
                patch("ontology_jev.server.OllamaProvider", create=True) as ollama:
            ollama.return_value.judge.return_value = observation
            jev.return_value.judge.return_value = observation
            status, result = self.request("/api/live", {
                "case_id": fixture["id"], "policy": initial["candidate_policy"],
                "provider": "ollama", "message": message, "model": model})
            self.assertEqual(status, 200)
            ollama.assert_called_once_with(model=model)
            jev.assert_not_called()
            sent = ollama.return_value.judge.call_args.args[0]
            self.assertEqual(sent["message"], message)
            self.assertNotIn("semantic", sent)
            self.assertEqual(result["case"], sent)
            self.assertEqual(result["semantic"], observation)
            for key in ("decision", "baseline_decision"):
                self.assertEqual(result[key]["semantic"]["model"], "actual:9b")
                self.assertFalse(result[key]["semantic"]["calibrated"])
                self.assertEqual(result[key]["semantic"]["score_kind"], "generated_self_report")
            # A provider receives a detached copy, including nested case facts.
            sent["customer"]["name"] = "Changed outside the fixture"
        self.assertEqual(self.request("/api/workspace")[1], initial)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_live_rejects_invalid_inputs_before_constructing_providers(self):
        _, initial = self.request("/api/workspace")
        base = {"case_id": initial["rows"][0]["case"]["id"],
                "policy": initial["candidate_policy"], "provider": "ollama"}
        invalid = [("provider", value) for value in (None, "fixture", "", 7, [])]
        invalid += [("message", value) for value in (None, "", " \t\n", 7, "x" * 4001)]
        invalid += [("model", value) for value in (None, "", "  ", 7, "x" * 121, "\u6a21\u578b", "a\nb", "a\x7fb")]
        invalid += [("case_id", value) for value in (None, "unknown", [])]
        invalid += [("policy", None), ("policy", dict(initial["candidate_policy"], refund_window_days=True))]
        invalid += [(field, "private-value") for field in
                    ("api_key", "base_url", "credentials", "authorization", "url")]
        with patch("ontology_jev.server.JevProvider") as jev, \
                patch("ontology_jev.server.OllamaProvider", create=True) as ollama:
            jev.return_value.judge.return_value = initial["rows"][0]["case"]["semantic"]
            ollama.return_value.judge.return_value = initial["rows"][0]["case"]["semantic"]
            for field, value in invalid:
                with self.subTest(field=field, value=value):
                    status, result = self.request("/api/live", dict(base, **{field: value}))
                    self.assertEqual(status, 400)
                    self.assertIn("error", result)
                    self.assertNotIn("private-value", result["error"])
            self.assertEqual(jev.call_count, 0)
            self.assertEqual(ollama.call_count, 0)

    def test_live_provider_errors_are_explicit_without_fixture_fallback_or_writes(self):
        _, initial = self.request("/api/workspace")
        for provider in ("ollama", "jev"):
            with self.subTest(provider=provider), \
                    patch("ontology_jev.server.JevProvider") as jev, \
                    patch("ontology_jev.server.OllamaProvider", create=True) as ollama:
                selected, other = (ollama, jev) if provider == "ollama" else (jev, ollama)
                selected.return_value.judge.side_effect = ProviderError("Provider unavailable; no retry was attempted.")
                other.return_value.judge.side_effect = ProviderError("Provider unavailable; no retry was attempted.")
                status, result = self.request("/api/live", {
                    "case_id": initial["rows"][0]["case"]["id"],
                    "policy": initial["candidate_policy"], "provider": provider, "model": "custom-model"})
                self.assertEqual(status, 502)
                self.assertEqual(result, {"error": "Provider unavailable; no retry was attempted."})
                selected.assert_called_once_with(model="custom-model")
                selected.return_value.judge.assert_called_once()
                other.assert_not_called()
        self.assertEqual(self.request("/api/workspace")[1], initial)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_invalid_policy_and_unknown_case_are_clear_errors(self):
        _, workspace = self.request("/api/workspace")
        policy = dict(workspace["candidate_policy"], refund_window_days=True)
        self.assertEqual(self.request("/api/compare", {"policy": policy})[0], 400)
        self.assertEqual(self.request("/api/regressions", {
            "case_id": "../../outside", "policy": workspace["candidate_policy"],
            "expected_outcome": "allow"})[0], 400)

    def test_regression_preserves_observation_and_detects_wrong_expectation(self):
        _, workspace = self.request("/api/workspace")
        row = workspace["rows"][0]
        payload = {"case_id": row["case"]["id"], "policy": workspace["candidate_policy"],
                   "expected_outcome": row["after"]["outcome"]}
        status, saved = self.request("/api/regressions", payload)
        self.assertEqual(status, 200)
        self.assertTrue(saved["saved"])
        self.assertEqual(self.request("/api/regressions", payload)[1]["count"], 1)
        report = run_regressions(Path(self.directory.name))
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["failed"], 0)
        payload["expected_outcome"] = "deny"
        self.request("/api/regressions", payload)
        report = run_regressions(Path(self.directory.name))
        self.assertEqual(report["failed"], 1)

    def test_cross_origin_mutation_is_rejected(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request("POST", "/api/compare", headers={
                "Content-Type": "application/json", "Content-Length": "14",
                "Origin": "https://unrelated.example"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            self.assertEqual(json.loads(response.read()), {"error": "Cross-origin requests are not allowed."})
        finally:
            connection.close()

    def test_replay_endpoint_checks_current_draft_without_model_calls_or_writes(self):
        _, workspace = self.request("/api/workspace")
        row = workspace["rows"][0]
        self.request("/api/regressions", {
            "case_id": row["case"]["id"], "policy": workspace["candidate_policy"],
            "expected_outcome": "allow"})
        folder = Path(self.directory.name) / "regressions"
        original = {path.name: path.read_bytes() for path in folder.glob("*.json")}
        with patch("ontology_jev.server.JevProvider.judge") as judge:
            status, saved = self.request("/api/regressions/run", {"mode": "saved"})
            self.assertEqual(status, 200)
            self.assertEqual((saved["mode"], saved["passed"]), ("saved", 1))
            status, draft = self.request("/api/regressions/run", {
                "mode": "draft", "policy": workspace["baseline_policy"]})
            self.assertEqual(status, 200)
            self.assertEqual((draft["mode"], draft["failed"]), ("draft", 1))
            self.assertEqual(draft["results"][0]["actual"], "deny")
            judge.assert_not_called()
        self.assertEqual({path.name: path.read_bytes() for path in folder.glob("*.json")}, original)

    def test_replay_endpoint_rejects_missing_ambiguous_or_invalid_mode(self):
        _, workspace = self.request("/api/workspace")
        for payload in ({}, {"mode": "unknown"}, {"mode": "draft"},
                        {"mode": "draft", "policy": None},
                        {"mode": "saved", "policy": workspace["baseline_policy"]},
                        {"mode": "saved", "policy": None}):
            with self.subTest(payload=payload):
                status, result = self.request("/api/regressions/run", payload)
                self.assertEqual(status, 400)
                self.assertIn("error", result)

    def test_corrupt_snapshot_does_not_break_workspace_or_replay(self):
        folder = Path(self.directory.name) / "regressions"
        folder.mkdir()
        (folder / "deep.json").write_text("[" * 1100 + "0" + "]" * 1100, encoding="utf-8")
        status, workspace = self.request("/api/workspace")
        self.assertEqual(status, 200)
        self.assertEqual(workspace["summary"]["total"], 20)
        status, listed = self.request("/api/regressions")
        self.assertEqual(status, 200)
        self.assertIn("error", listed["regressions"][0])
        status, report = self.request("/api/regressions/run", {"mode": "saved"})
        self.assertEqual(status, 200)
        self.assertEqual((report["total"], report["failed"]), (1, 1))
        self.assertIn("error", report["results"][0])

    def test_static_allowlist_and_no_secret_file_access(self):
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/../../.git/config")[0], 404)
        self.assertEqual(self.request("/api/not-real")[0], 404)

    def test_overflowing_snapshot_exponents_do_not_break_http_responses(self):
        _, workspace = self.request("/api/workspace")
        _, saved = self.request("/api/regressions", {
            "case_id": workspace["rows"][0]["case"]["id"],
            "policy": workspace["candidate_policy"], "expected_outcome": "allow"})
        folder = Path(self.directory.name) / "regressions"
        record = json.loads((folder / (saved["id"] + ".json")).read_text(encoding="utf-8"))
        record["case"]["title"] = "exponent-placeholder"
        (folder / "overflow.json").write_text(
            json.dumps(record).replace('"exponent-placeholder"', "1e309"), encoding="utf-8")
        self.assertEqual(self.request("/api/workspace")[0], 200)
        for path, body in (("/api/regressions/run", {"mode": "saved"}),
                           ("/api/regressions", None)):
            with self.subTest(path=path):
                status, response = self.request(path, body)
                self.assertEqual(status, 200)
                records = response["results"] if body else response["regressions"]
                self.assertEqual(len(records), 2)
                self.assertEqual({item["id"] for item in records if "error" in item}, {"overflow"})
                if body:
                    self.assertEqual((response["passed"], response["failed"]), (1, 1))
                json.dumps(response, allow_nan=False)

    def test_oversized_body_is_rejected_before_reading_any_body(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            # Send headers only: trying to read the declared body would time out.
            connection.request("POST", "/api/compare", headers={
                "Content-Type": "application/json", "Content-Length": str(65536 + 1)})
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            self.assertEqual(json.loads(response.read()), {"error": "Request body exceeds 64 KiB."})
        finally:
            connection.close()

    def test_invalid_json_is_rejected(self):
        request = Request(self.url + "/api/compare", data=b"not json",
                          headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as context:
            self.opener.open(request, timeout=5)
        self.assertEqual(context.exception.code, 400)
        context.exception.close()

    def test_malformed_unicode_and_deep_json_have_safe_errors(self):
        status, result = self.request("/api/compare", {"policy": {"\ud800": 1}})
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        request = Request(self.url + "/api/compare", data=b"[" * 1100 + b"0" + b"]" * 1100,
                          headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as context:
            self.opener.open(request, timeout=5)
        self.assertEqual(context.exception.code, 400)
        self.assertIn("error", json.loads(context.exception.read()))
        context.exception.close()


if __name__ == "__main__":
    unittest.main()
