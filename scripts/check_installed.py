"""Check an installed wheel independently of the checkout: python -I scripts/check_installed.py."""

import json
from pathlib import Path
import tempfile
import threading
from urllib import request

import ontology_jev
from ontology_jev.server import create_server


checkout = Path(__file__).resolve().parents[1]
installed = Path(ontology_jev.__file__).resolve()
source_package = checkout / "ontology_jev"
if source_package == installed or source_package in installed.parents:
    raise SystemExit("Check imported the source checkout; install the wheel in a separate environment and use -I.")

with tempfile.TemporaryDirectory(prefix="ontologyjev-installed-") as storage:
    with create_server(port=0, storage_dir=storage) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = "http://127.0.0.1:{}".format(server.server_port)
        opener = request.build_opener(request.ProxyHandler({}))
        try:
            for path, expected_type in (("/", "text/html"), ("/app.js", "text/javascript"),
                                        ("/workbench.js", "text/javascript"), ("/style.css", "text/css")):
                with opener.open(base + path, timeout=5) as response:
                    assert response.status == 200 and expected_type in response.headers["Content-Type"], path
                    assert response.read(), path
            with opener.open(base + "/api/workspace", timeout=5) as response:
                workspace = json.load(response)
            assert workspace["summary"]["total"] == 20
            assert workspace["summary"]["changed"] == 5
            print("Installed wheel: static assets and 20-case comparison passed.")
        finally:
            server.shutdown()
            worker.join(timeout=5)
