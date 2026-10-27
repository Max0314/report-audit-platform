import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.auth import hardware_auth_middleware, can_manage_library
from app.core.config import CqcLibrarySettings, get_settings


def make_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(hardware_auth_middleware)

    @app.get("/page")
    async def page():
        return PlainTextResponse("ok")

    @app.post("/api/write")
    async def write():
        return {"ok": True}

    return app


class AuthMiddlewareTests(unittest.TestCase):
    def test_library_allowlist_validates_and_defaults_closed(self):
        self.assertEqual(CqcLibrarySettings(admin_user_ids=' 013,11,12,11 ').admin_user_ids, '11,12,13')
        for value in ('*', '11,', '-1', '0', '1.0', '１１', 'name'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CqcLibrarySettings(admin_user_ids=value)
        with patch.object(get_settings().cqc_library, 'admin_user_ids', ''):
            self.assertFalse(can_manage_library({'id': 11, 'roles': ['super_admin']}))

    def test_library_permission_uses_only_verified_database_id(self):
        with patch.object(get_settings().cqc_library, 'admin_user_ids', '11,12,13'):
            for uid in (11, '12', 13):
                self.assertTrue(can_manage_library({'id': uid}))
            for uid in (None, '', True, 11.0, '１１', ' 11', 'other', 99):
                self.assertFalse(can_manage_library({'id': uid, 'name': 'authorized name',
                    'username': '11', 'userKey': '11', 'roles': ['super_admin'],
                    'capabilities': {'canManageModelConfig': True}}))

    def test_unauthenticated_api_returns_401(self):
        with patch("app.auth._verify", new=AsyncMock(return_value=(None, True))):
            with TestClient(make_app()) as client:
                response = client.post("/api/write")
        self.assertEqual(response.status_code, 401)

    def test_authenticated_page_issues_csrf_and_post_requires_header(self):
        user = {"id": 7, "username": "owner"}
        with patch("app.auth._verify", new=AsyncMock(return_value=(user, True))):
            with TestClient(make_app()) as client:
                page = client.get("/page")
                self.assertEqual(page.status_code, 200)
                token = client.cookies.get("csrf_token")
                self.assertTrue(token)
                denied = client.post("/api/write")
                allowed = client.post("/api/write", headers={"X-CSRF-Token": token})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

    def test_auth_backend_outage_returns_503(self):
        with patch("app.auth._verify", new=AsyncMock(return_value=(None, False))):
            with TestClient(make_app()) as client:
                response = client.get("/page")
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
