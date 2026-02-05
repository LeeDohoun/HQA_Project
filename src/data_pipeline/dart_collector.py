# 파일: src/data_pipeline/dart_collector.py
"""
DART 공시 수집기
- 전자공시시스템(DART) API를 통한 공시 데이터 수집
- 사업보고서, 분기보고서, 주요사항보고서 등
"""

import os
import requests
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass


@dataclass
class Disclosure:
    """공시 데이터 클래스"""
    corp_code: str          # 고유번호
    corp_name: str          # 회사명
    stock_code: str         # 종목코드
    report_nm: str          # 보고서명
    rcept_no: str           # 접수번호
    flr_nm: str             # 공시제출인명
    rcept_dt: str           # 접수일자
    rm: str                 # 비고
    
    @property
    def url(self) -> str:
        """DART 공시 조회 URL"""
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"


class DARTCollector:
    """DART 공시 수집기"""
    
    BASE_URL = "https://opendart.fss.or.kr/api"
    
    # 주요 보고서 유형
    REPORT_TYPES = {
        "A": "사업보고서",
        "B": "반기보고서", 
        "C": "분기보고서",
        "D": "등록법인결산서류",
        "E": "소액공모법인결산서류",
        "F": "주요사항보고서",
        "G": "주요경영사항신고",
        "H": "최대주주등소유주식변동신고서",
        "I": "거래소신고",
        "J": "공정위신고"
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: DART API 키 (없으면 환경변수 DART_API_KEY 사용)
        """
        self.api_key = api_key or os.getenv("DART_API_KEY")
        if not self.api_key:
            print("⚠️ DART API 키가 설정되지 않았습니다.")
            print("   발급: https://opendart.fss.or.kr/")
            print("   설정: .env 파일에 DART_API_KEY=your_key 추가")
    
    def get_corp_code(self, stock_code: str) -> Optional[str]:
        """
        종목코드로 DART 고유번호 조회
        
        Args:
            stock_code: 종목코드 (예: "005930")
            
        Returns:
            DART 고유번호 또는 None
        """
        # TODO: corp_code.xml 파일에서 매핑 조회
        # 다운로드: https://opendart.fss.or.kr/api/corpCode.xml
        pass
    
    def fetch_disclosures(
        self,
        corp_code: Optional[str] = None,
        stock_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        report_type: Optional[str] = None,
        max_count: int = 20
    ) -> List[Disclosure]:
        """
        공시 목록 조회
        
        Args:
            corp_code: DART 고유번호
            stock_code: 종목코드 (corp_code 없을 시 변환)
            start_date: 검색 시작일 (YYYYMMDD)
            end_date: 검색 종료일 (YYYYMMDD)
            report_type: 보고서 유형 (A~J)
            max_count: 최대 조회 개수
            
        Returns:
            Disclosure 리스트
        """
        if not self.api_key:
            print("❌ DART API 키가 필요합니다.")
            return []
        
        # 기본 날짜 설정 (최근 1년)
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        
        # 종목코드 -> 고유번호 변환
        if stock_code and not corp_code:
            corp_code = self.get_corp_code(stock_code)
        
        params = {
            "crtfc_key": self.api_key,
            "bgn_de": start_date,
            "end_de": end_date,
            "page_count": max_count
        }
        
        if corp_code:
            params["corp_code"] = corp_code
        if report_type:
            params["pblntf_ty"] = report_type
        
        try:
            response = requests.get(f"{self.BASE_URL}/list.json", params=params)
            data = response.json()
            
            if data.get("status") != "000":
                print(f"⚠️ DART API 오류: {data.get('message')}")
                return []
            
            disclosures = []
            for item in data.get("list", []):
                disclosures.append(Disclosure(
                    corp_code=item.get("corp_code", ""),
                    corp_name=item.get("corp_name", ""),
                    stock_code=item.get("stock_code", ""),
                    report_nm=item.get("report_nm", ""),
                    rcept_no=item.get("rcept_no", ""),
                    flr_nm=item.get("flr_nm", ""),
                    rcept_dt=item.get("rcept_dt", ""),
                    rm=item.get("rm", "")
                ))
            
            print(f"📋 {len(disclosures)}개 공시 조회 완료")
            return disclosures
            
        except Exception as e:
            print(f"❌ 공시 조회 오류: {e}")
            return []
    
    def fetch_document(self, rcept_no: str) -> Optional[str]:
        """
        공시 본문 조회
        
        Args:
            rcept_no: 접수번호
            
        Returns:
            공시 본문 텍스트
        """
        if not self.api_key:
            return None
        
        params = {
            "crtfc_key": self.api_key,
            "rcept_no": rcept_no
        }
        
        try:
            response = requests.get(f"{self.BASE_URL}/document.xml", params=params)
            # TODO: XML 파싱하여 본문 추출
            return response.text
        except Exception as e:
            print(f"❌ 문서 조회 오류: {e}")
            return None
    
    def fetch_financial_statements(
        self,
        corp_code: str,
        year: int,
        report_code: str = "11011"  # 사업보고서
    ) -> Dict:
        """
        재무제표 조회
        
        Args:
            corp_code: DART 고유번호
            year: 사업연도
            report_code: 보고서 코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)
            
        Returns:
            재무제표 데이터
        """
        if not self.api_key:
            return {}
        
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code
        }
        
        try:
            response = requests.get(f"{self.BASE_URL}/fnlttSinglAcnt.json", params=params)
            return response.json()
        except Exception as e:
            print(f"❌ 재무제표 조회 오류: {e}")
            return {}


# 테스트
if __name__ == "__main__":
    collector = DARTCollector()
    # API 키 설정 후 테스트
    # disclosures = collector.fetch_disclosures(stock_code="005930")
    print("DART Collector 초기화 완료")
