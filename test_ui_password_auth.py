import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import install_security_middleware
from config_loader import load_config
from run import _normalize_ui_next_path, install_web_routes


class UiPasswordConfigTest(unittest.TestCase):
    def test_ui_password_env_keeps_raw_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.toml").write_text(
                "\n".join(
                    [
                        "[server]",
                        'data_dir = "./data"',
                        "port = 8300",
                        "",
                        "[mcp]",
                        "http_port = 8200",
                        "sse_port = 8201",
                    ]
                ),
                "utf-8",
            )

            with patch.dict(os.environ, {"AGENTCHATTR_UI_PASSWORD": "jacky036816"}, clear=True):
                config = load_config(root)

        self.assertEqual(config["security"]["ui_password"], "jacky036816")


class UiPasswordHelperTest(unittest.TestCase):
    def test_normalize_next_path_allows_same_origin_paths(self):
        self.assertEqual(_normalize_ui_next_path("/thread"), "/thread")
        self.assertEqual(_normalize_ui_next_path("/settings?tab=ui"), "/settings?tab=ui")

    def test_normalize_next_path_rejects_unsafe_or_empty_values(self):
        for value in ("", "https://example.com", "http://127.0.0.1:8300/thread", "//evil.com", "thread"):
            with self.subTest(value=value):
                self.assertEqual(_normalize_ui_next_path(value), "/")


class UiPasswordRouteTest(unittest.TestCase):
    @contextmanager
    def _client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static_dir = root / "static"
            design_dir = root / "agentchattr_UI_design"
            static_dir.mkdir()
            design_dir.mkdir()
            (static_dir / "index.html").write_text(
                "<html><head><title>agentchattr</title></head><body>Chat UI</body></html>",
                "utf-8",
            )
            (design_dir / "thread.html").write_text(
                "<html><head><title>thread</title></head><body>Thread UI</body></html>",
                "utf-8",
            )

            app = FastAPI()
            install_web_routes(
                app,
                {"security": {"ui_password": "open-sesame"}},
                session_token="session-token",
                root=root,
                static_dir=static_dir,
            )
            with TestClient(app) as client:
                yield client

    def test_protected_ui_shows_login_without_session_token(self):
        with self._client() as client:
            for path in ("/", "/thread"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("UI password", response.text)
                    self.assertNotIn("window.__SESSION_TOKEN__", response.text)

    def test_wrong_password_does_not_set_auth_cookie(self):
        with self._client() as client:
            response = client.post(
                "/login",
                data={"password": "wrong", "next": "/thread"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("agentchattr_ui_auth", response.headers.get("set-cookie", ""))

    def test_correct_password_sets_session_cookie_and_unlocks_ui(self):
        with self._client() as client:
            response = client.post(
                "/login",
                data={"password": "open-sesame", "next": "/thread"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/thread")
            cookie = response.headers.get("set-cookie", "")
            self.assertIn("agentchattr_ui_auth=", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=lax", cookie)
            self.assertNotIn("Max-Age", cookie)

            unlocked = client.get("/thread")

        self.assertEqual(unlocked.status_code, 200)
        self.assertIn('window.__SESSION_TOKEN__="session-token"', unlocked.text)
        self.assertIn("Thread UI", unlocked.text)

    def test_api_without_session_token_still_returns_403(self):
        app = FastAPI()
        install_security_middleware(
            app,
            token="session-token",
            cfg={"server": {"port": 8300, "allowed_origins": []}},
        )

        @app.get("/api/messages")
        async def messages():
            return JSONResponse([])

        with TestClient(app) as client:
            response = client.get("/api/messages")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "forbidden: invalid or missing session token")


if __name__ == "__main__":
    unittest.main()
