"""Public calculator preview using the real handler, with no clinic data."""
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="doc4docs-pricing-preview-") as directory:
        os.environ["DATA_DIR"] = directory
        import app
        from auth_store import AuthStore
        from auth_http import LoginLimiter
        store = AuthStore(Path(directory) / "auth.sqlite3")
        store.initialize()
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        server.auth_store = store
        server.login_limiter = LoginLimiter()
        print(f"http://127.0.0.1:{server.server_port}/precificacao", flush=True)
        with patch.object(app, "db", side_effect=AssertionError("No clinic data in pricing preview")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("No external calls in preview")):
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
