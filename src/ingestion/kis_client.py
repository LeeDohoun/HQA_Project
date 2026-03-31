from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

import requests


class KISClient:
    _shared_access_token = ""
    _shared_token_expire_at = 0.0
    _shared_token_lock = threading.Lock()

    def __init__(self):
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        self.base_url = os.getenv(
            "KIS_BASE_URL",
            "https://openapi.koreainvestment.com:9443",
        ).rstrip("/")
        self.session = requests.Session()
        self._access_token = ""

    @classmethod
    def _has_valid_shared_token(cls) -> bool:
        return bool(cls._shared_access_token) and time.time() < cls._shared_token_expire_at

    @classmethod
    def _clear_shared_token(cls) -> None:
        cls._shared_access_token = ""
        cls._shared_token_expire_at = 0.0

    def _token_url(self) -> str:
        return f"{self.base_url}/oauth2/tokenP"

    def issue_token(self) -> str:
        if self._access_token and self._has_valid_shared_token():
            return self._access_token

        if self._has_valid_shared_token():
            self._access_token = self._shared_access_token
            return self._access_token

        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY/KIS_APP_SECRET 환경변수가 필요합니다.")

        with self._shared_token_lock:
            if self._has_valid_shared_token():
                self._access_token = self._shared_access_token
                return self._access_token

            response = self.session.post(
                self._token_url(),
                headers={"content-type": "application/json; charset=utf-8"},
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
                timeout=20,
            )
            response.raise_for_status()

            payload = response.json()
            token = payload.get("access_token", "").strip()
            if not token:
                raise ValueError(f"KIS access token 발급 실패: {payload}")

            expires_in = int(payload.get("expires_in", 0) or 0)
            expire_at = time.time() + max(300, expires_in - 60) if expires_in > 0 else time.time() + (60 * 60)

            self._shared_access_token = token
            self._shared_token_expire_at = expire_at
            self._access_token = token
            return self._access_token

    def invalidate_token(self) -> None:
        self._access_token = ""
        self._clear_shared_token()

    def build_headers(self, tr_id: str) -> Dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.issue_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }

    def request(self, path: str, params: Optional[Dict] = None, tr_id: str = "") -> Dict:
        url = f"{self.base_url}{path}"
        response = self.session.get(
            url,
            params=params or {},
            headers=self.build_headers(tr_id=tr_id),
            timeout=20,
        )

        # 단일 토큰 정책/만료로 인증이 실패할 경우 1회 재발급 후 재시도
        if response.status_code in {401, 403}:
            self.invalidate_token()
            response = self.session.get(
                url,
                params=params or {},
                headers=self.build_headers(tr_id=tr_id),
                timeout=20,
            )

        response.raise_for_status()
        return response.json()