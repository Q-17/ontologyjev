"""Local Ollama wire contracts; no installed model or network is required."""

import io
import json
import math
import unittest
from http.client import IncompleteRead
from unittest.mock import Mock, patch
from urllib import request
from urllib.error import HTTPError, URLError

from ontology_jev import providers


def case():
    return {
        "message": "Please refund my payment.",
        "customer": {"id": "private-customer"},
        "payment": {"id": "private-payment", "amount": 900},
        "semantic": {"refund_request": 0.94, "model": "fixture-v1"},
    }


def answer(score=0.98):
    return {
        "model": "qwen3.5:9b", "created_at": "2026-10-23T00:00:00Z",
        "message": {"role": "assistant", "content": json.dumps({
            "refund_request": score, "explanation": "The customer explicitly requests a refund.",
        })},
        "done": True, "done_reason": "stop", "total_duration": 100000000,
        "load_duration": 1000000, "prompt_eval_count": 44,
        "prompt_eval_duration": 20000000, "eval_count": 20, "eval_duration": 79000000,
    }


class Response(io.BytesIO):
    def __init__(self, payload, status=200):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        super().__init__(raw)
        self.status = status
        self.read_limit = None

    def read(self, size=-1):
        self.read_limit = size
        return super().read(size)


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(providers, "OllamaProvider"), "OllamaProvider must be implemented")
        self.transport = Mock()
        patcher = patch("ontology_jev.providers.request.build_opener", return_value=self.transport)
        self.build_opener = patcher.start()
        self.addCleanup(patcher.stop)

    def respond(self, payload=None, status=200):
        response = Response(answer() if payload is None else payload, status)
        self.transport.open.return_value = response
        return response

    def test_native_chat_contract_sends_message_only_with_bounded_generation(self):
        self.respond()
        result = providers.OllamaProvider().judge(case())
        self.transport.open.assert_called_once()
        args, kwargs = self.transport.open.call_args
        sent = args[0]
        self.assertEqual(sent.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(sent.method, "POST")
        self.assertEqual(sent.get_header("Content-type"), "application/json")
        self.assertIsNone(sent.get_header("Authorization"))
        self.assertEqual(kwargs["timeout"], 120)
        body = json.loads(sent.data)
        self.assertEqual(body["model"], "qwen3.5:9b")
        self.assertEqual(body["options"], {"temperature": 0, "num_predict": 256})
        self.assertIs(body["think"], False)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["keep_alive"], "5m")
        self.assertEqual(body["messages"][1:], [{"role": "user", "content": case()["message"]}])
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertIn("refund", body["messages"][0]["content"])
        self.assertNotIn("private-customer", sent.data.decode())
        self.assertNotIn("private-payment", sent.data.decode())
        self.assertNotIn("fixture-v1", sent.data.decode())
        schema = body["format"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(set(schema["required"]), {"refund_request", "explanation"})
        self.assertEqual(schema["properties"]["refund_request"], {"type": "number", "minimum": 0, "maximum": 1})
        self.assertEqual(schema["properties"]["explanation"]["type"], "string")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(result["raw_request"], body)

    def test_result_preserves_uncalibrated_provenance_raw_evidence_and_timing(self):
        payload = answer()
        payload["model"] = "qwen3.5:9b-canonical"
        self.respond(payload)
        result = providers.OllamaProvider(model="local-model", timeout=5).judge(case())
        self.assertEqual(result["refund_request"], 0.98)
        self.assertEqual(result["source"], "ollama")
        self.assertEqual(result["score_kind"], "generated_self_report")
        self.assertIs(result["calibrated"], False)
        self.assertEqual(result["model"], payload["model"])
        self.assertEqual(result["explanation"], "The customer explicitly requests a refund.")
        self.assertEqual(result["raw_response"], payload)
        self.assertEqual(result["usage"], {"input_tokens": 44, "output_tokens": 20})
        self.assertTrue(math.isfinite(result["elapsed_seconds"]))
        self.assertGreaterEqual(result["elapsed_seconds"], 0)
        self.assertEqual(result["raw_request"]["model"], "local-model")
        self.assertEqual(self.transport.open.call_args[1]["timeout"], 5)
        json.dumps(result, allow_nan=False)

    def test_local_transport_disables_environment_proxy_and_redirects(self):
        self.respond()
        providers.OllamaProvider().judge(case())
        handlers = self.build_opener.call_args[0]
        proxies = [handler for handler in handlers if isinstance(handler, request.ProxyHandler)]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].proxies, {})
        redirects = [handler for handler in handlers if isinstance(handler, request.HTTPRedirectHandler)]
        self.assertEqual(len(redirects), 1)
        self.assertIsNone(redirects[0].redirect_request(Mock(), Mock(), 302, "redirect", {}, "https://remote.test"))

    def test_configuration_rejects_remote_cloud_and_invalid_values_before_network(self):
        invalid = [
            {"model": value} for value in (None, "", " ", "model\nname", "qwen:cloud", "qwen-cloud:9b",
                                          "QWEN:CLOUD", "https://ollama.com/model", "model?token=secret")
        ] + [
            {"timeout": value} for value in (None, 0, -1, True, "120", 121, float("nan"), float("inf"), 10 ** 400)
        ] + [
            {"base_url": value} for value in (
                None, "", "http://example.com", "https://example.com", "ftp://localhost",
                "http://127.0.0.1.evil.test", "http://user:secret@localhost", "http://localhost?token=secret",
                "http://localhost/?", "http://localhost/#", "http://localhost/#fragment",
                "http://localhost:0", "http://localhost:99999", "http://localhost:invalid",
                "http://localhost\n", "http://localhost\\@remote.test",
            )
        ]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(providers.ProviderError):
                providers.OllamaProvider(**kwargs)
        self.transport.open.assert_not_called()

    def test_loopback_ipv4_ipv6_and_localhost_urls_are_supported(self):
        for base in ("http://127.0.0.1:11434/", "http://[::1]:11434", "http://localhost:11434", "https://localhost:11434"):
            with self.subTest(base=base):
                self.respond()
                providers.OllamaProvider(base_url=base).judge(case())
                self.assertEqual(self.transport.open.call_args[0][0].full_url, base.rstrip("/") + "/api/chat")

    def test_invalid_messages_do_not_open_network(self):
        for item in (None, {}, [], {"message": None}, {"message": 7}, {"message": "  "}):
            with self.subTest(item=item), self.assertRaises(providers.ProviderError):
                providers.OllamaProvider().judge(item)
        self.transport.open.assert_not_called()

    def test_transport_errors_are_explicit_redacted_and_not_retried(self):
        for failure in (
            URLError("private-response"), TimeoutError("private-response"), IncompleteRead(b"private-response", 40),
            HTTPError("http://localhost", 502, "private-response", {}, io.BytesIO(b"private-response")),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.transport.open.reset_mock()
                self.transport.open.side_effect = failure
                with self.assertRaises(providers.ProviderError) as raised:
                    providers.OllamaProvider().judge(case())
                self.assertNotIn("private-response", str(raised.exception))
                self.transport.open.assert_called_once()

    def test_unsuccessful_status_and_oversized_response_are_rejected(self):
        for payload, status in ((b"private-response", 503), (b" " * (1024 * 1024 + 1), 200)):
            with self.subTest(status=status):
                response = self.respond(payload, status)
                with self.assertRaises(providers.ProviderError) as raised:
                    providers.OllamaProvider().judge(case())
                self.assertNotIn("private-response", str(raised.exception))
                if status == 200:
                    self.assertEqual(response.read_limit, 1024 * 1024 + 1)

    def test_malformed_envelope_content_or_error_payload_is_rejected(self):
        malformed = [b"private-response", b"\xff", [], {}, {"error": "private-response"},
                     dict(answer(), model=""), dict(answer(), model="qwen:cloud"),
                     dict(answer(), message={"role": "user", "content": answer()["message"]["content"]}),
                     dict(answer(), message={"role": "assistant", "content": 4}),
                     dict(answer(), message={"role": "assistant", "content": "```json\n{}\n```"})]
        for content in ({}, [], {"refund_request": 0.9}, {"refund_request": 0.9, "explanation": 7}):
            payload = answer()
            payload["message"]["content"] = json.dumps(content)
            malformed.append(payload)
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(providers.ProviderError) as raised:
                self.respond(payload)
                providers.OllamaProvider().judge(case())
            self.assertNotIn("private-response", str(raised.exception))

    def test_incomplete_and_truncated_generations_are_rejected_even_with_valid_json(self):
        for value in (False, None, 1, "true"):
            with self.subTest(done=value), self.assertRaises(providers.ProviderError):
                self.respond(dict(answer(), done=value))
                providers.OllamaProvider().judge(case())
        for reason in (None, "length", "load", "unload", ""):
            with self.subTest(reason=reason), self.assertRaises(providers.ProviderError):
                self.respond(dict(answer(), done_reason=reason))
                providers.OllamaProvider().judge(case())

    def test_scores_reject_bool_nonfinite_non_numeric_and_out_of_range(self):
        for score in (True, False, None, "0.98", float("nan"), float("inf"), -0.01, 1.01, 10 ** 400):
            with self.subTest(score=score), self.assertRaises(providers.ProviderError):
                self.respond(answer(score))
                providers.OllamaProvider().judge(case())
        for score in (0, 0.5, 1):
            with self.subTest(score=score):
                self.respond(answer(score))
                result = providers.OllamaProvider().judge(case())
                self.assertEqual(result["refund_request"], score)
                self.assertIsInstance(result["refund_request"], float)

    def test_full_raw_response_must_be_finite_json_even_for_unused_fields(self):
        for value in ("NaN", "Infinity", "1e999"):
            raw = json.dumps(dict(answer(), extra={"unused": 13})).replace('"unused": 13', '"unused": ' + value)
            with self.subTest(value=value), self.assertRaises(providers.ProviderError):
                self.respond(raw.encode("utf-8"))
                providers.OllamaProvider().judge(case())

    def test_usage_is_optional_and_present_token_counts_are_nonnegative_integers(self):
        payload = answer()
        del payload["prompt_eval_count"]
        del payload["eval_count"]
        self.respond(payload)
        self.assertNotIn("usage", providers.OllamaProvider().judge(case()))
        for field in ("prompt_eval_count", "eval_count"):
            for value in (-1, True, "20", 1.5, None):
                with self.subTest(field=field, value=value), self.assertRaises(providers.ProviderError):
                    self.respond(dict(answer(), **{field: value}))
                    providers.OllamaProvider().judge(case())


if __name__ == "__main__":
    unittest.main()
