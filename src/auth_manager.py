import json
import os
from typing import Optional
from urllib import request, error

from config import Config

REGISTRATION_DISABLED_MESSAGE = (
    "Registration is disabled. Use the tester credentials provided to you."
)


class AuthManager:
    def __init__(self, backend_url: Optional[str] = None):
        self.backend_url = (backend_url or Config.BACKEND_URL).rstrip("/")
        self.access_token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.email: Optional[str] = None
        self.token_type: str = "bearer"
        self._token_path = os.path.join(
            os.path.expanduser("~"), ".pixelpilot", "auth.json"
        )
        self._load_token()

    @property
    def is_logged_in(self) -> bool:
        return bool(self.access_token)

    def _load_token(self) -> None:
        try:
            if not os.path.exists(self._token_path):
                return
            with open(self._token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.access_token = data.get("access_token")
            self.user_id = data.get("user_id")
            self.email = data.get("email")
            self.token_type = data.get("token_type", "bearer")
        except Exception:
            self.access_token = None
            self.user_id = None
            self.email = None
            self.token_type = "bearer"

    def _save_token(self, token: dict) -> None:
        os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
        payload = {
            "access_token": token.get("access_token"),
            "user_id": token.get("user_id"),
            "email": token.get("email"),
            "token_type": token.get("token_type", "bearer"),
        }
        with open(self._token_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _clear_token(self) -> None:
        self.access_token = None
        self.user_id = None
        self.email = None
        self.token_type = "bearer"
        try:
            if os.path.exists(self._token_path):
                os.remove(self._token_path)
        except Exception:
            pass

    def _request_json(
        self,
        method: str,
        path: str,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        url = f"{self.backend_url}{path}"
        payload = None
        if data is not None:
            payload = json.dumps(data).encode("utf-8")

        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        req = request.Request(url, data=payload, method=method, headers=req_headers)
        try:
            with request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as e:
            detail = "Request failed"
            try:
                body = e.read().decode("utf-8")
                if body:
                    data = json.loads(body)
                    detail = data.get("detail", detail)
            except Exception:
                pass
            raise RuntimeError(detail) from e
        except error.URLError as e:
            raise RuntimeError("Backend unavailable. Is it running?") from e

    def login(self, email: str, password: str) -> None:
        token = self._request_json(
            "POST", "/auth/login", {"email": email, "password": password}
        )
        self.access_token = token.get("access_token")
        self.user_id = token.get("user_id")
        self.email = token.get("email")
        self.token_type = token.get("token_type", "bearer")
        if not self.access_token:
            raise RuntimeError("Login failed: no access token returned")
        self._save_token(token)

    def register(self, email: str, password: str) -> None:
        # Temporarily disabled while tester accounts are managed manually.
        # token = self._request_json(
        #     "POST", "/auth/register", {"email": email, "password": password}
        # )
        # self.access_token = token.get("access_token")
        # self.user_id = token.get("user_id")
        # self.email = token.get("email")
        # self.token_type = token.get("token_type", "bearer")
        # if not self.access_token:
        #     raise RuntimeError("Registration failed: no access token returned")
        # self._save_token(token)
        raise RuntimeError(REGISTRATION_DISABLED_MESSAGE)

    def verify_token(self) -> bool:
        if not self.access_token:
            return False

        try:
            data = self._request_json(
                "GET",
                "/auth/me",
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            self.user_id = data.get("user_id", self.user_id)
            self.email = data.get("email", self.email)
            return True
        except RuntimeError:
            return False

    def logout(self) -> None:
        self._clear_token()


_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
