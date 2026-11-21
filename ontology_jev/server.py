"""Local-only HTTP workbench with no third-party runtime dependencies."""

import copy
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .engine import compare, evaluate, validate_policy
from .fixtures import BASELINE_POLICY, DRAFT_POLICY, ONTOLOGY, load_cases
from .providers import JevProvider, OllamaProvider, ProviderError
from .regressions import list_regressions, run_regressions, save_regression


_STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
           "/index.html": ("index.html", "text/html; charset=utf-8"),
           "/style.css": ("style.css", "text/css; charset=utf-8"),
           "/app.js": ("app.js", "text/javascript; charset=utf-8"),
           "/workbench.js": ("workbench.js", "text/javascript; charset=utf-8")}
_MAX_BODY = 65536


def create_server(host="127.0.0.1", port=8765, storage_dir=None):
    """Create a loopback workbench; the caller owns its serving lifecycle."""
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("The workbench only supports localhost binding.")
    storage_dir = Path(storage_dir) if storage_dir is not None else Path.cwd() / ".ontologyjev"
    cases = load_cases()
    by_id = {case["id"]: case for case in cases}
    static_dir = Path(__file__).parent / "static"

    def workspace(policy):
        result = compare(cases, BASELINE_POLICY, policy)
        result.update({"ontology": ONTOLOGY,
                       "provider": {"mode": "fixture", "label": "Synthetic fixture", "live": False,
                                    "ollama_model": "qwen3.5:9b",
                                    "jev_available": bool(os.environ.get("TYPESAFE_API_KEY", "").strip())},
                       "regression_count": len(list_regressions(storage_dir))})
        return result

    class Handler(BaseHTTPRequestHandler):
        server_version = "OntologyJev/0.1"

        def log_message(self, format, *args):
            # Case messages and API credentials never go to request logs.
            pass

        def send_content(self, status, content, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, status, data):
            self.send_content(status, json.dumps(data, ensure_ascii=True, allow_nan=False).encode("utf-8"),
                              "application/json; charset=utf-8")

        def valid_host(self):
            allowed = {"127.0.0.1:%d" % self.server.server_port, "localhost:%d" % self.server.server_port}
            return self.headers.get("Host") in allowed

        def do_GET(self):
            if not self.valid_host():
                self.send_json(403, {"error": "Only the local workbench host is allowed."})
                return
            path = self.path.partition("?")[0]
            if path == "/api/workspace":
                self.send_json(200, workspace(DRAFT_POLICY))
            elif path == "/api/regressions":
                self.send_json(200, {"regressions": list_regressions(storage_dir)})
            elif path in _STATIC:
                filename, content_type = _STATIC[path]
                try:
                    self.send_content(200, (static_dir / filename).read_bytes(), content_type)
                except OSError:
                    self.send_json(404, {"error": "Static asset is unavailable."})
            else:
                self.send_json(404, {"error": "Not found."})

        def do_POST(self):
            if not self.valid_host() or self.headers.get("Origin") not in (None, "http://" + self.headers.get("Host", "")):
                self.send_json(403, {"error": "Cross-origin requests are not allowed."})
                return
            path = self.path.partition("?")[0]
            if path not in ("/api/compare", "/api/regressions", "/api/regressions/run", "/api/live"):
                self.send_json(404, {"error": "Not found."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_json(400, {"error": "Invalid Content-Length."})
                return
            if size > _MAX_BODY:
                self.send_json(413, {"error": "Request body exceeds 64 KiB."})
                return
            if size <= 0 or self.headers.get_content_type() != "application/json":
                self.send_json(400, {"error": "A JSON request body is required."})
                return
            try:
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("Request body must be a JSON object.")
                if path == "/api/regressions/run":
                    mode = body.get("mode")
                    if mode not in ("saved", "draft"):
                        raise ValueError("mode must be saved or draft.")
                    if mode == "saved":
                        if "policy" in body:
                            raise ValueError("Saved replay uses snapshot policies; omit policy.")
                        policy = None
                    else:
                        policy = validate_policy(body.get("policy"))
                    self.send_json(200, run_regressions(storage_dir, policy=policy))
                    return
                policy = validate_policy(body.get("policy"))
                if path == "/api/compare":
                    self.send_json(200, workspace(policy))
                    return
                case_id = body.get("case_id")
                if not isinstance(case_id, str) or case_id not in by_id:
                    raise ValueError("Unknown case_id.")
                case = by_id[case_id]
                if path == "/api/regressions":
                    self.send_json(200, save_regression(storage_dir, case, policy, case["semantic"], body.get("expected_outcome")))
                else:
                    if set(body) - {"case_id", "policy", "provider", "message", "model"}:
                        raise ValueError("Live requests accept only case_id, policy, provider, message, and model; credentials and endpoint URLs must stay server-side.")
                    provider = body.get("provider", "jev")
                    if provider not in ("ollama", "jev"):
                        raise ValueError("provider must be ollama or jev.")
                    case = copy.deepcopy(case)
                    case.pop("semantic", None)
                    if "message" in body:
                        message = body["message"]
                        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
                            raise ValueError("message must be a nonblank string of at most 4000 characters.")
                        case["message"] = message
                    options = {}
                    if "model" in body:
                        model = body["model"]
                        if (not isinstance(model, str) or not model.strip() or len(model) > 120
                                or any(ord(character) < 32 or ord(character) > 126 for character in model)):
                            raise ValueError("model must be a nonblank ASCII string of at most 120 characters without control characters.")
                        options["model"] = model
                    selected = OllamaProvider if provider == "ollama" else JevProvider
                    semantic = selected(**options).judge(case)
                    self.send_json(200, {"case": case, "policy": policy, "semantic": semantic,
                                         "baseline_decision": evaluate(case, BASELINE_POLICY, semantic),
                                         "decision": evaluate(case, policy, semantic)})
            except RecursionError:
                self.send_json(400, {"error": "JSON nesting is too deep."})
            except (ValueError, UnicodeError) as error:
                self.send_json(400, {"error": str(error)})
            except ProviderError as error:
                self.send_json(502, {"error": str(error)})
            except OSError:
                self.send_json(500, {"error": "Unable to read or write local workbench storage."})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
