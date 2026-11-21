"""Refund-intent observations, never payment authorization or execution.

Fixtures are synthetic demonstrations, not model calibration evidence. Jev is
opt-in and receives only the supplied customer message, without account facts.
"""

import copy
import ipaddress
import json
import math
import os
import time
from http.client import HTTPException
from urllib import error, parse, request


_MAX_RESPONSE_BYTES = 1024 * 1024
_REFUND_QUESTION = (
    "Does the customer explicitly request a refund or reversal of a payment? "
    "Judge only the message; this does not authorize any action."
)


class ProviderError(Exception):
    """A safe, user-facing provider failure without credentials or raw bodies."""


class FixtureProvider:
    """Replay explicitly synthetic observations without network access."""

    def judge(self, case):
        if not isinstance(case, dict) or not isinstance(case.get("semantic"), dict):
            raise ProviderError("This case has no synthetic semantic observation.")
        observation = copy.deepcopy(case["semantic"])
        observation["source"] = "synthetic_fixture"
        observation.setdefault("model", "fixture-v1")
        return observation


class _NoRedirect(request.HTTPRedirectHandler):
    """Do not forward a bearer credential to a redirected endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(base_url):
    if not isinstance(base_url, str) or not base_url or any(
        character.isspace() or ord(character) < 32 or character == "\\"
        for character in base_url
    ):
        raise ProviderError("Invalid provider base URL.")
    try:
        parts = parse.urlsplit(base_url)
        hostname = parts.hostname
        port = parts.port
        if not hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        if parts.query or parts.fragment or "?" in base_url or "#" in base_url:
            raise ValueError()
        if port is not None and port < 1:
            raise ValueError()
        loopback = hostname.lower() == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                pass
        if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
            raise ValueError()
    except (ValueError, TypeError):
        raise ProviderError("Use an HTTPS provider URL, or HTTP on loopback only; omit credentials, queries, and fragments.") from None
    return base_url.rstrip("/") + "/v1/systemone"


def _reject_json_constant(value):
    raise ValueError("Nonfinite JSON number")


def _observation(payload):
    if not isinstance(payload, dict):
        raise ProviderError("Jev returned an invalid result object.")
    model = payload.get("model")
    answers = payload.get("answers")
    if not isinstance(model, str) or not model.strip() or not isinstance(answers, dict):
        raise ProviderError("Jev returned a result without model or answer metadata.")
    answer = answers.get("refund_request")
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        raise ProviderError("Jev returned no valid refund-request Noul answer.")
    probability = answer.get("noul")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise ProviderError("Jev returned an invalid refund-request probability.")
    try:
        probability = float(probability)
    except (ValueError, OverflowError):
        raise ProviderError("Jev returned an invalid refund-request probability.") from None
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ProviderError("Jev returned a refund-request probability outside [0, 1].")
    result = {"refund_request": probability, "source": "jev", "model": model}
    if "usage" in payload:
        if not isinstance(payload["usage"], dict):
            raise ProviderError("Jev returned invalid usage metadata.")
        usage = {}
        for field in ("input_tokens", "output_tokens"):
            if field in payload["usage"]:
                count = payload["usage"][field]
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    raise ProviderError("Jev returned an invalid token count in usage metadata.")
                usage[field] = count
        # Preserve documented counts only; arbitrary metadata is not evidence.
        result["usage"] = usage
    return result


class JevProvider:
    """Make one semantic-only Jev request, with no automatic retries.

    ``refund_request`` describes intent; it cannot authorize a refund. The key
    is taken from TYPESAFE_API_KEY when api_key is omitted, and stays server-side.
    HTTP is accepted only for local, compatible model endpoints.
    """

    def __init__(self, api_key=None, model="jev-latest", base_url="https://api.typesafe.ai", timeout=20):
        key = os.environ.get("TYPESAFE_API_KEY") if api_key is None else api_key
        if not isinstance(key, str) or not key.strip() or any(ord(c) < 32 or ord(c) > 126 for c in key):
            raise ProviderError("A valid TYPESAFE_API_KEY is required for live Jev requests.")
        if not isinstance(model, str) or not model.strip():
            raise ProviderError("A nonempty Jev model name is required.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ProviderError("Provider timeout must be a finite number from 0 to 120 seconds, excluding 0.")
        try:
            valid_timeout = math.isfinite(timeout) and 0 < timeout <= 120
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ProviderError("Provider timeout must be a finite number from 0 to 120 seconds, excluding 0.")
        self._api_key = key.strip()
        self.model = model
        self.timeout = timeout
        self._endpoint = _endpoint(base_url)

    def judge(self, case):
        if not isinstance(case, dict) or not isinstance(case.get("message"), str) or not case["message"].strip():
            raise ProviderError("A nonempty customer message is required for semantic judgment.")
        body = {
            "model": self.model,
            "state": {"message": case["message"]},
            "questions": {"refund_request": {"type": "noul", "instructions": _REFUND_QUESTION}},
        }
        sent = request.Request(
            self._endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with request.build_opener(_NoRedirect()).open(sent, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise ProviderError("Jev returned an unsuccessful HTTP status.")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            exc.close()
            raise ProviderError("Jev request failed with HTTP status {}.".format(exc.code)) from None
        except (error.URLError, OSError, HTTPException):
            raise ProviderError("Jev request failed or timed out; no automatic retry was attempted.") from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ProviderError("Jev response exceeded the 1 MiB size limit.")
        try:
            payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        except (UnicodeError, ValueError, RecursionError):
            raise ProviderError("Jev returned malformed JSON.") from None
        return _observation(payload)


def _ollama_endpoint(base_url):
    if not isinstance(base_url, str) or not base_url or any(
        character.isspace() or ord(character) < 32 or character == "\\"
        for character in base_url
    ):
        raise ProviderError("Invalid local Ollama base URL.")
    try:
        parts = parse.urlsplit(base_url)
        hostname = parts.hostname
        port = parts.port
        if not hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        if parts.query or parts.fragment or "?" in base_url or "#" in base_url:
            raise ValueError()
        if port is not None and port < 1:
            raise ValueError()
        loopback = hostname.lower() == "localhost"
        if not loopback:
            loopback = ipaddress.ip_address(hostname).is_loopback
        if not loopback or parts.scheme not in ("http", "https"):
            raise ValueError()
    except (ValueError, TypeError):
        raise ProviderError("Ollama requires a loopback HTTP(S) URL without credentials, queries, or fragments.") from None
    return base_url.rstrip("/") + "/api/chat"


def _local_ollama_model(model):
    if (
        not isinstance(model, str) or not model
        or any(not c.isascii() or not (c.isalnum() or c in "._-/:") for c in model)
        or "://" in model or ":cloud" in model.lower() or "-cloud" in model.lower()
    ):
        raise ProviderError("A local Ollama model name is required; cloud models and URLs are unsupported.")
    return model


def _ollama_observation(payload):
    if not isinstance(payload, dict) or "error" in payload:
        raise ProviderError("Ollama returned an invalid result object.")
    if payload.get("done") is not True or payload.get("done_reason") != "stop":
        raise ProviderError("Ollama generation was incomplete or truncated.")
    model = _local_ollama_model(payload.get("model"))
    message = payload.get("message")
    if (
        not isinstance(message, dict) or message.get("role") != "assistant"
        or not isinstance(message.get("content"), str)
    ):
        raise ProviderError("Ollama returned no valid assistant message.")
    try:
        generated = json.loads(message["content"], parse_constant=_reject_json_constant)
        json.dumps(generated, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ProviderError("Ollama returned malformed generated JSON.") from None
    if not isinstance(generated, dict) or not isinstance(generated.get("explanation"), str):
        raise ProviderError("Ollama returned no valid explanation.")
    score = generated.get("refund_request")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ProviderError("Ollama returned an invalid refund-request score.")
    try:
        score = float(score)
    except (ValueError, OverflowError):
        raise ProviderError("Ollama returned an invalid refund-request score.") from None
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ProviderError("Ollama returned a refund-request score outside [0, 1].")
    result = {
        "refund_request": score, "source": "ollama", "model": model,
        "score_kind": "generated_self_report", "calibrated": False,
        "explanation": generated["explanation"], "raw_response": payload,
    }
    usage = {}
    for field, name in (("prompt_eval_count", "input_tokens"), ("eval_count", "output_tokens")):
        if field in payload:
            count = payload[field]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ProviderError("Ollama returned an invalid token count in usage metadata.")
            usage[name] = count
    if usage:
        result["usage"] = usage
    return result


class OllamaProvider:
    """Generate an explicitly uncalibrated intent score using a local model.

    Only the customer message is sent. Account facts and policy decisions stay
    outside this request; a generated score cannot authorize a refund. Failures
    are explicit, with no retries or substitute synthetic observations.
    """

    def __init__(self, model="qwen3.5:9b", base_url="http://127.0.0.1:11434", timeout=120):
        self.model = _local_ollama_model(model)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ProviderError("Provider timeout must be a finite number from 0 to 120 seconds, excluding 0.")
        try:
            valid_timeout = math.isfinite(timeout) and 0 < timeout <= 120
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ProviderError("Provider timeout must be a finite number from 0 to 120 seconds, excluding 0.")
        self.timeout = timeout
        self._endpoint = _ollama_endpoint(base_url)

    def judge(self, case):
        if not isinstance(case, dict) or not isinstance(case.get("message"), str) or not case["message"].strip():
            raise ProviderError("A nonempty customer message is required for semantic judgment.")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _REFUND_QUESTION + (
                    " Return JSON with refund_request, a score from 0 (no refund request)"
                    " to 1 (explicit refund request), and a brief explanation."
                )},
                {"role": "user", "content": case["message"]},
            ],
            "format": {
                "type": "object",
                "properties": {
                    "refund_request": {"type": "number", "minimum": 0, "maximum": 1},
                    "explanation": {"type": "string"},
                },
                "required": ["refund_request", "explanation"],
                "additionalProperties": False,
            },
            "options": {"temperature": 0, "num_predict": 256},
            "think": False, "stream": False, "keep_alive": "5m",
        }
        sent = request.Request(
            self._endpoint, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            # Only this loopback transport bypasses environment proxies.
            with request.build_opener(request.ProxyHandler({}), _NoRedirect()).open(sent, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise ProviderError("Ollama returned an unsuccessful HTTP status.")
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            exc.close()
            raise ProviderError("Ollama request failed with HTTP status {}.".format(exc.code)) from None
        except (error.URLError, OSError, HTTPException):
            raise ProviderError("Ollama request failed or timed out; no automatic retry was attempted.") from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ProviderError("Ollama response exceeded the 1 MiB size limit.")
        try:
            payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
            # Preserve the complete response only if every nested value is
            # serializable finite JSON, including otherwise-unused metadata.
            json.dumps(payload, allow_nan=False)
        except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
            raise ProviderError("Ollama returned malformed or nonfinite JSON.") from None
        result = _ollama_observation(payload)
        result["raw_request"] = body
        result["elapsed_seconds"] = time.perf_counter() - started
        return result
