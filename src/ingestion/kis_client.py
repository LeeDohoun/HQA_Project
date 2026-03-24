from __future__ import annotations

import os
from typing import Dict, Optional

import requests


class KISClient:
    def __init__(self):
        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
        self.session = requests.Session()
        self._access_token = ""

    def _token_url(self) -> str:
        return f"{self.base_url}/oauth2/tokenP"

    def issue_token(self) -> str:
        if self._access_token:
            return self._access_token
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY/KIS_APP_SECRET 환경변수가 필요합니다.")

        response = self.session.post(
            self._token_url(),
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload.get("access_token", "")
        if not self._access_token:
            raise ValueError("KIS access token 발급 실패")
        return self._access_token

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
        response.raise_for_status()
        return response.json()
