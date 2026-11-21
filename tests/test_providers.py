"""Provider wire contracts, entirely offline with a mocked HTTP boundary."""

import copy
import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from ontology_jev.providers import FixtureProvider, JevProvider, ProviderError


def case():
    return {
        "id": "refund-request", "message": "Please refund my payment.",
        "customer": {"id": "private-customer"},
        "semantic": {"refund_request": 0.94, "model": "fixture-v1"},
    }


def answer(score=0.98):
    return {
        "model": "jev-1.13.0",
        "answers": {"refund_request": {"type": "noul", "noul": score}},
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }


class Response(io.BytesIO):
    def __init__(self, payload, status=200):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        super().__init__(raw)
        self.status = status


class FixtureTests(unittest.TestCase):
    def test_fixture_copies_observation_and_identifies_synthetic_source(self):
        item = case()
        item["semantic"]["source"] = "jev"
        item["semantic"]["metadata"] = {"label": "demonstration"}
        del item["semantic"]["model"]
        original = copy.deepcopy(item)
        result = FixtureProvider().judge(item)
        self.assertEqual(result["source"], "synthetic_fixture")
        self.assertEqual(result["model"], "fixture-v1")
        self.assertEqual(result["refund_request"], 0.94)
        result["metadata"]["label"] = "changed"
        self.assertEqual(item, original)

    def test_fixture_rejects_missing_observation(self):
        for value in (None, {}, {"semantic": None}, {"semantic": []}):
            with self.subTest(value=value), self.assertRaises(ProviderError):
                FixtureProvider().judge(value)


class JevTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.patcher = patch("ontology_jev.providers.request.build_opener", return_value=self.transport)
        self.build_opener = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def respond(self, payload=None, status=200):
        self.transport.open.return_value = Response(answer() if payload is None else payload, status)

    def test_official_post_contract_sends_message_only_and_returns_observation(self):
        self.respond()
        result = JevProvider(api_key="test-key").judge(case())
        self.assertEqual(result, {
            "refund_request": 0.98, "source": "jev", "model": "jev-1.13.0",
            "usage": {"input_tokens": 120, "output_tokens": 12},
        })
        self.transport.open.assert_called_once()
        args, kwargs = self.transport.open.call_args
        sent = args[0]
        self.assertEqual(sent.full_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(sent.method, "POST")
        self.assertEqual(sent.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(sent.get_header("Content-type"), "application/json")
        self.assertEqual(kwargs["timeout"], 20)
        self.assertEqual(json.loads(sent.data), {
            "model": "jev-latest",
            "state": {"message": "Please refund my payment."},
            "questions": {"refund_request": {
                "type": "noul",
                "instructions": "Does the customer explicitly request a refund or reversal of a payment? Judge only the message; this does not authorize any action.",
            }},
        })

    def test_environment_key_and_explicit_model_are_supported(self):
        self.respond()
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "environment-key"}):
            result = JevProvider(model="jev-version", timeout=5).judge(case())
        sent = self.transport.open.call_args[0][0]
        self.assertEqual(sent.get_header("Authorization"), "Bearer environment-key")
        self.assertEqual(json.loads(sent.data)["model"], "jev-version")
        self.assertEqual(result["model"], "jev-1.13.0")

    def test_absent_key_and_invalid_configuration_do_not_open_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError):
                JevProvider().judge(case())
        for kwargs in ({"api_key": ""}, {"api_key": "a\r\nb"}, {"model": ""},
                       {"timeout": 0}, {"timeout": True}, {"timeout": float("inf")}):
            arguments = {"api_key": "test-key"}
            arguments.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ProviderError):
                JevProvider(**arguments).judge(case())
        self.transport.open.assert_not_called()

    def test_invalid_messages_do_not_open_network(self):
        for item in (None, {}, {"message": None}, {"message": "  "}, {"message": 7}, []):
            with self.subTest(item=item), self.assertRaises(ProviderError):
                JevProvider(api_key="test-key").judge(item)
        self.transport.open.assert_not_called()

    def test_only_https_or_loopback_http_base_urls_are_accepted(self):
        for base in ("http://example.com", "ftp://localhost", "https://user:secret@example.com",
                     "https://example.com?token=secret", "https://example.com/#fragment",
                     "https://example.com:invalid", "https://", "https://example.com\n"):
            with self.subTest(base=base), self.assertRaises(ProviderError):
                JevProvider(api_key="test-key", base_url=base).judge(case())
        for base in ("http://127.0.0.1:8123", "http://[::1]:8123", "http://localhost:8123",
                     "https://example.com/gateway/"):
            with self.subTest(base=base):
                self.respond()
                JevProvider(api_key="test-key", base_url=base).judge(case())
                sent = self.transport.open.call_args[0][0]
                self.assertEqual(sent.full_url, base.rstrip("/") + "/v1/systemone")

    def test_redirects_are_rejected_before_authorization_can_be_forwarded(self):
        self.respond()
        JevProvider(api_key="test-key").judge(case())
        handler = self.build_opener.call_args[0][0]
        redirected = handler.redirect_request(Mock(), Mock(), 302, "redirect", {}, "https://other.test")
        self.assertIsNone(redirected)

    def test_network_and_http_failures_are_redacted_and_not_retried(self):
        for failure in (
            URLError("test-key private-response"), TimeoutError("test-key private-response"),
            HTTPError("https://api.typesafe.ai", 401, "test-key private-response", {}, io.BytesIO(b"private-response")),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.transport.open.reset_mock()
                self.transport.open.side_effect = failure
                with self.assertRaises(ProviderError) as raised:
                    JevProvider(api_key="test-key").judge(case())
                self.assertNotIn("test-key", str(raised.exception))
                self.assertNotIn("private-response", str(raised.exception))
                self.transport.open.assert_called_once()

    def test_non_success_response_and_oversized_body_are_rejected(self):
        for payload, status in ((b"private-response", 503), (b" " * (1024 * 1024 + 1), 200)):
            self.respond(payload, status)
            with self.assertRaises(ProviderError) as raised:
                JevProvider(api_key="test-key").judge(case())
            self.assertNotIn("private-response", str(raised.exception))

    def test_malformed_or_incomplete_provider_results_are_rejected(self):
        malformed = [b"private-response", b"\xff", [], {},
                     {"model": "jev-v1", "answers": {}},
                     {"answers": {"refund_request": {"type": "noul", "noul": 0.9}}},
                     dict(answer(), model=""), dict(answer(), usage=[])]
        wrong_type = answer()
        wrong_type["answers"]["refund_request"]["type"] = "boolean"
        malformed.append(wrong_type)
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(ProviderError) as raised:
                self.respond(payload)
                JevProvider(api_key="test-key").judge(case())
            self.assertNotIn("private-response", str(raised.exception))

    def test_probabilities_are_finite_numbers_including_zero_and_one(self):
        for score in (True, False, None, "0.98", float("nan"), float("inf"), -0.01, 1.01, 10 ** 400):
            with self.subTest(score=score), self.assertRaises(ProviderError):
                self.respond(answer(score))
                JevProvider(api_key="test-key").judge(case())
        for score in (0, 1, 0.5):
            with self.subTest(score=score):
                payload = answer(score)
                del payload["usage"]
                self.respond(payload)
                result = JevProvider(api_key="test-key").judge(case())
                self.assertEqual(result["refund_request"], score)
                self.assertIsInstance(result["refund_request"], float)
                self.assertNotIn("usage", result)

    def test_usage_rejects_nonfinite_and_malformed_known_token_counts(self):
        raw = json.dumps(answer()).replace('"input_tokens": 120', '"input_tokens": 1e999').encode("utf-8")
        self.respond(raw)
        with self.assertRaises(ProviderError):
            JevProvider(api_key="test-key").judge(case())
        for field in ("input_tokens", "output_tokens"):
            for value in (-1, True, False, 1.0, "12", None, [], {}):
                with self.subTest(field=field, value=value):
                    payload = answer()
                    payload["usage"][field] = value
                    self.respond(payload)
                    with self.assertRaises(ProviderError):
                        JevProvider(api_key="test-key").judge(case())

    def test_usage_omits_unsupported_metadata_and_is_strictly_json_serializable(self):
        payload = answer()
        payload["usage"] = {"input_tokens": 0, "output_tokens": 12, "unsupported": {"amount": 1}}
        raw = json.dumps(payload).replace('"amount": 1', '"amount": 1e999').encode("utf-8")
        self.respond(raw)
        result = JevProvider(api_key="test-key").judge(case())
        self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": 12})
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
