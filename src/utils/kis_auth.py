# 파일: src/utils/kis_auth.py
"""
한국투자증권 Open API 인증 모듈

공식 GitHub 참고:
https://github.com/koreainvestment/open-trading-api

기능:
- 접근 토큰 발급 및 관리
- API 호출 공통 함수
- 실전/모의투자 환경 전환
"""

import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from src.config.settings import get_data_dir, load_project_env

load_project_env()


# ==========================================
# 설정
# ==========================================
class KISConfig:
    """한국투자증권 API 설정"""
    
    # 실전투자
    PROD_DOMAIN = "https://openapi.koreainvestment.com:9443"
    
    # 모의투자
    VTS_DOMAIN = "https://openapivts.koreainvestment.com:29443"
    
    # 토큰 저장 경로
    TOKEN_DIR = get_data_dir() / "token"
    
    # API 키 (환경변수에서 로드)
    # 모의투자 전용 값이 있으면 paper=True 호출에서 우선 사용합니다.
    APP_KEY = os.getenv("KIS_APP_KEY") or os.getenv("KIS_API_KEY")
    APP_SECRET = os.getenv("KIS_APP_SECRET") or os.getenv("KIS_API_SECRET")
    ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
    PAPER_APP_KEY = os.getenv("KIS_PAPER_APP_KEY") or os.getenv("KIS_VTS_APP_KEY")
    PAPER_APP_SECRET = os.getenv("KIS_PAPER_APP_SECRET") or os.getenv("KIS_VTS_APP_SECRET")
    PAPER_ACCOUNT_NO = os.getenv("KIS_PAPER_ACCOUNT_NO") or os.getenv("KIS_VTS_ACCOUNT_NO")

    @classmethod
    def get_app_key(cls, paper: bool = False) -> str:
        if paper:
            return cls.PAPER_APP_KEY or cls.APP_KEY or ""
        return cls.APP_KEY or ""

    @classmethod
    def get_app_secret(cls, paper: bool = False) -> str:
        if paper:
            return cls.PAPER_APP_SECRET or cls.APP_SECRET or ""
        return cls.APP_SECRET or ""

    @classmethod
    def get_account_no(cls, paper: bool = False) -> str:
        if paper:
            return cls.PAPER_ACCOUNT_NO or cls.ACCOUNT_NO or ""
        return cls.ACCOUNT_NO or ""
    
    # 계좌번호 파싱 (12345678-01 → CANO: 12345678, ACNT_PRDT_CD: 01)
    @classmethod
    def get_account(cls, paper: bool = False) -> tuple[str, str]:
        """계좌번호를 CANO, ACNT_PRDT_CD로 분리"""
        acc = cls.get_account_no(paper).replace("-", "")
        if len(acc) >= 10:
            return acc[:8], acc[8:10]
        elif len(acc) >= 8:
            return acc[:8], "01"
        return "", ""


# ==========================================
# 토큰 관리
# ==========================================
class KISToken:
    """접근 토큰 관리"""
    
    _instance = None
    _access_token: Optional[str] = None
    _token_expired: Optional[datetime] = None
    _is_paper: bool = False  # 모의투자 여부
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @property
    def domain(self) -> str:
        """현재 도메인 반환"""
        return KISConfig.VTS_DOMAIN if self._is_paper else KISConfig.PROD_DOMAIN
    
    @property
    def is_valid(self) -> bool:
        """토큰 유효성 확인"""
        if not self._access_token or not self._token_expired:
            return False
        # 만료 10분 전에 갱신
        return datetime.now() < self._token_expired - timedelta(minutes=10)
    
    def get_token(self, paper: bool = False) -> str:
        """
        접근 토큰 반환 (없거나 만료되면 발급)
        
        Args:
            paper: True면 모의투자, False면 실전투자
        """
        if self._is_paper != paper:
            self._access_token = None
            self._token_expired = None
            self._is_paper = paper
        
        # 캐시된 토큰이 유효하면 반환
        if self.is_valid:
            return self._access_token
        
        # 파일에서 로드 시도
        if self._load_token_from_file():
            return self._access_token
        
        # 새로 발급
        return self._issue_token()
    
    def _issue_token(self) -> str:
        """토큰 발급"""
        url = f"{self.domain}/oauth2/tokenP"
        
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": KISConfig.get_app_key(self._is_paper),
            "appsecret": KISConfig.get_app_secret(self._is_paper),
        }

        if not body["appkey"] or not body["appsecret"]:
            print("[ERROR] KIS API 키/시크릿이 설정되지 않았습니다.")
            return ""
        
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
            self._access_token = data.get("access_token")
            
            # 만료 시간 파싱 (예: "2024-01-01 12:00:00")
            expires_str = data.get("access_token_token_expired", "")
            if expires_str:
                self._token_expired = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
            else:
                # 기본 24시간
                self._token_expired = datetime.now() + timedelta(hours=24)
            
            # 파일에 저장
            self._save_token_to_file()
            
            print(f"[OK] KIS 토큰 발급 완료 (만료: {self._token_expired})")
            return self._access_token
            
        except Exception as e:
            print(f"[ERROR] 토큰 발급 실패: {e}")
            return ""
    
    def _get_token_file(self) -> Path:
        """토큰 파일 경로"""
        mode = "paper" if self._is_paper else "prod"
        return KISConfig.TOKEN_DIR / f"kis_token_{mode}.json"
    
    def _save_token_to_file(self):
        """토큰을 파일에 저장"""
        try:
            KISConfig.TOKEN_DIR.mkdir(parents=True, exist_ok=True)
            
            data = {
                "access_token": self._access_token,
                "expired": self._token_expired.isoformat() if self._token_expired else None,
                "is_paper": self._is_paper,
            }
            
            with open(self._get_token_file(), "w") as f:
                json.dump(data, f)
                
        except Exception as e:
            print(f"[WARN] 토큰 저장 실패: {e}")
    
    def _load_token_from_file(self) -> bool:
        """파일에서 토큰 로드"""
        try:
            token_file = self._get_token_file()
            if not token_file.exists():
                return False
            
            with open(token_file, "r") as f:
                data = json.load(f)
            
            self._access_token = data.get("access_token")
            expired_str = data.get("expired")
            
            if expired_str:
                self._token_expired = datetime.fromisoformat(expired_str)
            
            # 유효성 재확인
            return self.is_valid
            
        except Exception:
            return False


# ==========================================
# API 호출 함수
# ==========================================
_token_manager = KISToken()


def get_hashkey(body: Dict[str, Any], paper: bool = False) -> str:
    """주문성 API용 hashkey 생성."""
    domain = KISConfig.VTS_DOMAIN if paper else KISConfig.PROD_DOMAIN
    url = f"{domain}/uapi/hashkey"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KISConfig.get_app_key(paper),
        "appsecret": KISConfig.get_app_secret(paper),
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("HASH") or data.get("hash") or ""
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] hashkey 생성 실패: {e}")
        return ""


def get_base_headers(
    tr_id: str,
    paper: bool = False,
    body: Optional[Dict[str, Any]] = None,
    hashkey: bool = False,
) -> Dict[str, str]:
    """
    API 호출용 기본 헤더 생성
    
    Args:
        tr_id: 거래 ID (API마다 다름)
        paper: 모의투자 여부
    """
    token = _token_manager.get_token(paper)
    
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KISConfig.get_app_key(paper),
        "appsecret": KISConfig.get_app_secret(paper),
        "tr_id": tr_id,
        "custtype": "P",
    }
    if hashkey and body is not None:
        headers["hashkey"] = get_hashkey(body, paper=paper)
    return headers


def call_api(
    method: str,
    path: str,
    tr_id: str,
    params: Optional[Dict] = None,
    body: Optional[Dict] = None,
    paper: bool = False,
    hashkey: bool = False,
) -> Dict[str, Any]:
    """
    한국투자증권 API 호출 공통 함수
    
    Args:
        method: "GET" 또는 "POST"
        path: API 경로 (예: "/uapi/domestic-stock/v1/quotations/inquire-price")
        tr_id: 거래 ID
        params: GET 파라미터
        body: POST 바디
        paper: 모의투자 여부
        
    Returns:
        API 응답 딕셔너리
    """
    domain = KISConfig.VTS_DOMAIN if paper else KISConfig.PROD_DOMAIN
    url = f"{domain}{path}"
    headers = get_base_headers(tr_id, paper, body=body, hashkey=hashkey)
    
    try:
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=10)
        else:
            resp = requests.post(url, headers=headers, json=body, timeout=10)
        
        resp.raise_for_status()
        return resp.json()
        
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] API 호출 실패: {e}")
        return {"rt_cd": "-1", "msg1": str(e)}


def is_api_available(paper: bool = False) -> bool:
    """API 사용 가능 여부 확인"""
    return bool(KISConfig.get_app_key(paper) and KISConfig.get_app_secret(paper))


def is_trading_api_available(paper: bool = False) -> bool:
    """주문 API 사용 가능 여부 확인."""
    app_ready = bool(KISConfig.get_app_key(paper) and KISConfig.get_app_secret(paper))
    cano, acnt_prdt_cd = KISConfig.get_account(paper)
    return app_ready and bool(cano and acnt_prdt_cd)


# ==========================================
# 단독 테스트
# ==========================================
if __name__ == "__main__":
    print("=== KIS Auth 테스트 ===")
    print(f"APP_KEY 설정됨: {bool(KISConfig.APP_KEY)}")
    print(f"APP_SECRET 설정됨: {bool(KISConfig.APP_SECRET)}")
    print(f"계좌번호: {KISConfig.ACCOUNT_NO}")
    
    if is_api_available():
        token = _token_manager.get_token(paper=False)
        print(f"토큰 (앞 20자): {token[:20]}...")
    else:
        print("[WARN] API 키가 설정되지 않았습니다.")
        print("   .env 파일에 KIS_APP_KEY, KIS_APP_SECRET을 설정하세요.")
