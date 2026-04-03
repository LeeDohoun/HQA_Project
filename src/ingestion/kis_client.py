from __future__ import annotations

# File role:
# - Thin KIS HTTP client with shared token caching and refresh handling.

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import requests

# 프로세스 재시작 시에도 토큰을 재사용하기 위한 파일 캐시 경로
# KIS 토큰은 유효기간 1일이나 하루 발급 횟수에 제한이 있으므로 파일로 유지
_TOKEN_CACHE_PATH = Path(os.path.expanduser("~")) / ".kis_token_cache.json"


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

    @classmethod
    def _load_file_cache(cls) -> bool:
        """파일 캐시에서 유효한 토큰을 메모리로 로드. 성공 시 True."""
        try:
            if not _TOKEN_CACHE_PATH.exists():
                return False
            data = json.loads(_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            token = data.get("access_token", "").strip()
            expire_at = float(data.get("expire_at", 0))
            if token and time.time() < expire_at:
                cls._shared_access_token = token
                cls._shared_token_expire_at = expire_at
                return True
        except Exception:
            pass
        return False

    @classmethod
    def _save_file_cache(cls) -> None:
        """현재 메모리 토큰을 파일 캐시에 저장."""
        try:
            _TOKEN_CACHE_PATH.write_text(
                json.dumps({
                    "access_token": cls._shared_access_token,
                    "expire_at": cls._shared_token_expire_at,
                }),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _token_url(self) -> str:
        return f"{self.base_url}/oauth2/tokenP"

    def issue_token(self) -> str:
        # 1순위: 인스턴스 토큰이 유효하면 바로 반환
        if self._access_token and self._has_valid_shared_token():
            return self._access_token

        # 2순위: 같은 프로세스 내 공유 메모리 토큰
        if self._has_valid_shared_token():
            self._access_token = self._shared_access_token
            return self._access_token

        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY/KIS_APP_SECRET 환경변수가 필요합니다.")

        with self._shared_token_lock:
            # 락 획득 후 재확인 (다른 스레드가 이미 발급했을 수 있음)
            if self._has_valid_shared_token():
                self._access_token = self._shared_access_token
                return self._access_token

            # 3순위: 파일 캐시 — 프로세스 재시작 후에도 토큰 재사용 (일일 발급 제한 대응)
            if self._load_file_cache():
                print("[KIS] 파일 캐시에서 토큰 로드 (재발급 생략)")
                self._access_token = self._shared_access_token
                return self._access_token

            # 4순위: 신규 발급
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

            # 클래스 변수를 self.로 쓰면 인스턴스 변수가 생성되어 classmethod에서 읽히지 않음
            # type(self) 또는 클래스명으로 명시 갱신
            type(self)._shared_access_token = token
            type(self)._shared_token_expire_at = expire_at
            self._access_token = token

            # 발급된 토큰을 파일에 저장 → 다음 프로세스 실행 시 재사용
            self._save_file_cache()
            print(f"[KIS] 신규 토큰 발급 완료, 캐시 저장: {_TOKEN_CACHE_PATH}")
            return self._access_token

    def invalidate_token(self) -> None:
        """토큰 만료 처리. 파일 캐시도 함께 삭제."""
        self._access_token = ""
        self._clear_shared_token()
        try:
            if _TOKEN_CACHE_PATH.exists():
                _TOKEN_CACHE_PATH.unlink()
        except Exception:
            pass

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

        # 401(토큰 만료)일 때만 재발급 후 재시도
        # 403은 rate limit / 권한 문제일 수 있어 재발급 시도 금지 (연쇄 403 방지)
        if response.status_code == 401:
            self.invalidate_token()
            response = self.session.get(
                url,
                params=params or {},
                headers=self.build_headers(tr_id=tr_id),
                timeout=20,
            )

        response.raise_for_status()
        return response.json()
