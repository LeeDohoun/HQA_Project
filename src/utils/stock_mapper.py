# 파일: src/utils/stock_mapper.py
"""
종목 매핑 유틸리티

종목명 ↔ 종목코드 변환
- 한국투자증권 공식 마스터 파일 활용 (가장 정확)
- FinanceDataReader 폴백
- 캐싱으로 빠른 조회

데이터 소스:
- https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info
"""

import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# 캐시 파일 경로
CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
CACHE_FILE = CACHE_DIR / "krx_stocks.json"
CACHE_EXPIRY_DAYS = 7  # 7일마다 갱신


@dataclass
class StockInfo:
    """종목 정보"""
    code: str           # 종목코드
    name: str           # 종목명
    market: str         # 시장 (KOSPI, KOSDAQ)
    sector: str = ""    # 업종
    

class StockMapper:
    """
    종목 매핑 클래스
    
    데이터 소스 우선순위:
    1. 캐시 파일 (있으면 사용)
    2. 한국투자증권 공식 마스터 파일 (API 키 불필요)
    3. FinanceDataReader
    4. 하드코딩 폴백
    
    Example:
        mapper = StockMapper()
        
        # 종목명 → 코드
        code = mapper.get_code("삼성전자")  # "005930"
        
        # 코드 → 종목명
        name = mapper.get_name("005930")    # "삼성전자"
        
        # 검색
        results = mapper.search("삼성")     # [{"name": "삼성전자", "code": "005930"}, ...]
    """
    
    _instance = None
    _stocks: Dict[str, StockInfo] = {}      # code → StockInfo
    _name_to_code: Dict[str, str] = {}      # name → code
    _initialized: bool = False
    
    def __new__(cls):
        """싱글톤 패턴"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._load_stocks()
            StockMapper._initialized = True
    
    def _load_stocks(self):
        """종목 데이터 로드 (캐시 → API → 폴백)"""
        
        # 1. 캐시 확인
        if self._load_from_cache():
            print(f"✅ 종목 데이터 캐시 로드 완료 ({len(self._stocks)}개)")
            return
        
        # 2. API에서 로드 시도
        if self._load_from_api():
            self._save_to_cache()
            print(f"✅ 종목 데이터 API 로드 완료 ({len(self._stocks)}개)")
            return
        
        # 3. 폴백 (하드코딩)
        self._load_fallback()
        print(f"⚠️ 종목 데이터 폴백 사용 ({len(self._stocks)}개)")
    
    def _load_from_cache(self) -> bool:
        """캐시 파일에서 로드"""
        try:
            if not CACHE_FILE.exists():
                return False
            
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 캐시 만료 확인
            cached_date = datetime.fromisoformat(data.get("updated", "2000-01-01"))
            if datetime.now() - cached_date > timedelta(days=CACHE_EXPIRY_DAYS):
                print("⚠️ 캐시 만료됨, 갱신 필요")
                return False
            
            # 데이터 로드
            for stock in data.get("stocks", []):
                info = StockInfo(
                    code=stock["code"],
                    name=stock["name"],
                    market=stock.get("market", ""),
                    sector=stock.get("sector", ""),
                )
                self._stocks[info.code] = info
                self._name_to_code[info.name] = info.code
            
            return len(self._stocks) > 0
            
        except Exception as e:
            print(f"⚠️ 캐시 로드 실패: {e}")
            return False
    
    def _load_from_api(self) -> bool:
        """API에서 종목 리스트 로드"""
        
        # 방법 1: 한국투자증권 공식 마스터 파일 (가장 정확)
        if self._load_from_kis_master():
            return True
        
        # 방법 2: FinanceDataReader (대안)
        if self._load_from_fdr():
            return True
        
        return False
    
    def _load_from_kis_master(self) -> bool:
        """
        한국투자증권 공식 종목 마스터 파일에서 로드
        
        출처: https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info
        - KOSPI: https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip
        - KOSDAQ: https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip
        """
        import urllib.request
        import ssl
        import zipfile
        import tempfile
        
        try:
            ssl._create_default_https_context = ssl._create_unverified_context
            
            with tempfile.TemporaryDirectory() as tmp_dir:
                # KOSPI 마스터 다운로드 및 파싱
                kospi_count = self._download_and_parse_kis_master(
                    url="https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
                    tmp_dir=tmp_dir,
                    market="KOSPI",
                    filename="kospi_code"
                )
                
                # KOSDAQ 마스터 다운로드 및 파싱
                kosdaq_count = self._download_and_parse_kis_master(
                    url="https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
                    tmp_dir=tmp_dir,
                    market="KOSDAQ",
                    filename="kosdaq_code"
                )
                
                print(f"   KOSPI: {kospi_count}개, KOSDAQ: {kosdaq_count}개")
                return len(self._stocks) > 0
                
        except Exception as e:
            print(f"⚠️ KIS 마스터 파일 로드 실패: {e}")
            return False
    
    def _download_and_parse_kis_master(
        self, 
        url: str, 
        tmp_dir: str, 
        market: str,
        filename: str
    ) -> int:
        """
        KIS 마스터 파일 다운로드 및 파싱
        
        파싱 로직 출처:
        https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/kis_kospi_code_mst.py
        
        파일 구조:
        - 앞부분: 단축코드(9) + 표준코드(12) + 한글명(나머지-228)
        - 뒷부분: 228바이트 고정폭 필드들
        """
        import urllib.request
        import zipfile
        import os
        
        count = 0
        zip_path = os.path.join(tmp_dir, f"{filename}.zip")
        mst_path = os.path.join(tmp_dir, f"{filename}.mst")
        
        try:
            # 다운로드
            urllib.request.urlretrieve(url, zip_path)
            
            # 압축 해제
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(tmp_dir)
            
            # 파싱 (공식 로직 참고)
            with open(mst_path, mode="r", encoding="cp949") as f:
                for row in f:
                    try:
                        # 앞부분 파싱 (뒤 228바이트 제외)
                        rf1 = row[0:len(row) - 228]
                        
                        # 단축코드: 0:9 (공백 제거)
                        code = rf1[0:9].strip()
                        
                        # 표준코드: 9:21 (사용 안함)
                        # standard_code = rf1[9:21].strip()
                        
                        # 한글명: 21: (공백 제거)
                        name = rf1[21:].strip()
                        
                        if code and name and len(code) == 6:
                            info = StockInfo(code=code, name=name, market=market)
                            self._stocks[code] = info
                            self._name_to_code[name] = code
                            count += 1
                            
                    except Exception:
                        continue
            
        except Exception as e:
            print(f"⚠️ {market} 마스터 파싱 오류: {e}")
        
        return count
    
    def _load_from_fdr(self) -> bool:
        """FinanceDataReader에서 로드"""
        try:
            import FinanceDataReader as fdr
            
            # KOSPI
            kospi = fdr.StockListing("KOSPI")
            if kospi is not None:
                for _, row in kospi.iterrows():
                    code = str(row.get("Code", "")).zfill(6)
                    name = row.get("Name", "")
                    sector = row.get("Sector", "")
                    if code and name:
                        info = StockInfo(code=code, name=name, market="KOSPI", sector=sector)
                        self._stocks[code] = info
                        self._name_to_code[name] = code
            
            # KOSDAQ
            kosdaq = fdr.StockListing("KOSDAQ")
            if kosdaq is not None:
                for _, row in kosdaq.iterrows():
                    code = str(row.get("Code", "")).zfill(6)
                    name = row.get("Name", "")
                    sector = row.get("Sector", "")
                    if code and name:
                        info = StockInfo(code=code, name=name, market="KOSDAQ", sector=sector)
                        self._stocks[code] = info
                        self._name_to_code[name] = code
            
            return len(self._stocks) > 0
            
        except ImportError:
            print("⚠️ FinanceDataReader 미설치")
            return False
        except Exception as e:
            print(f"⚠️ FDR 로드 실패: {e}")
            return False
    
    def _load_fallback(self):
        """하드코딩 폴백 (API 실패 시)"""
        fallback_stocks = [
            # 대형주 (시가총액 상위)
            ("005930", "삼성전자", "KOSPI", "반도체"),
            ("000660", "SK하이닉스", "KOSPI", "반도체"),
            ("373220", "LG에너지솔루션", "KOSPI", "2차전지"),
            ("207940", "삼성바이오로직스", "KOSPI", "바이오"),
            ("005380", "현대차", "KOSPI", "자동차"),
            ("000270", "기아", "KOSPI", "자동차"),
            ("068270", "셀트리온", "KOSPI", "바이오"),
            ("105560", "KB금융", "KOSPI", "금융"),
            ("055550", "신한지주", "KOSPI", "금융"),
            ("005490", "POSCO홀딩스", "KOSPI", "철강"),
            ("035420", "NAVER", "KOSPI", "플랫폼"),
            ("035720", "카카오", "KOSPI", "플랫폼"),
            ("006400", "삼성SDI", "KOSPI", "2차전지"),
            ("051910", "LG화학", "KOSPI", "화학"),
            ("012330", "현대모비스", "KOSPI", "자동차부품"),
            ("028260", "삼성물산", "KOSPI", "건설"),
            ("096770", "SK이노베이션", "KOSPI", "정유"),
            ("003670", "포스코퓨처엠", "KOSPI", "2차전지"),
            ("034020", "두산에너빌리티", "KOSPI", "중공업"),
            ("012450", "한화에어로스페이스", "KOSPI", "방산"),
            ("329180", "HD현대중공업", "KOSPI", "조선"),
            ("009150", "삼성전기", "KOSPI", "전자부품"),
            ("066570", "LG전자", "KOSPI", "가전"),
            ("003550", "LG", "KOSPI", "지주"),
            ("018260", "삼성에스디에스", "KOSPI", "IT서비스"),
            
            # KOSDAQ 대형주
            ("247540", "에코프로비엠", "KOSDAQ", "2차전지"),
            ("086520", "에코프로", "KOSDAQ", "2차전지"),
            ("323410", "카카오뱅크", "KOSPI", "금융"),
            ("259960", "크래프톤", "KOSPI", "게임"),
            ("352820", "하이브", "KOSPI", "엔터"),
            ("263750", "펄어비스", "KOSDAQ", "게임"),
            ("293490", "카카오게임즈", "KOSDAQ", "게임"),
            ("035760", "CJ ENM", "KOSPI", "엔터"),
            ("041510", "에스엠", "KOSDAQ", "엔터"),
            ("122870", "와이지엔터테인먼트", "KOSDAQ", "엔터"),
        ]
        
        for code, name, market, sector in fallback_stocks:
            info = StockInfo(code=code, name=name, market=market, sector=sector)
            self._stocks[code] = info
            self._name_to_code[name] = code
        
        # 별칭 추가
        aliases = {
            "현대자동차": "005380",
            "포스코홀딩스": "005490",
            "네이버": "035420",
            "NAVER": "035420",
        }
        for alias, code in aliases.items():
            if code in self._stocks:
                self._name_to_code[alias] = code
    
    def _save_to_cache(self):
        """캐시 파일에 저장"""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            
            data = {
                "updated": datetime.now().isoformat(),
                "count": len(self._stocks),
                "stocks": [
                    {
                        "code": info.code,
                        "name": info.name,
                        "market": info.market,
                        "sector": info.sector,
                    }
                    for info in self._stocks.values()
                ]
            }
            
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            print(f"✅ 종목 캐시 저장 완료: {CACHE_FILE}")
            
        except Exception as e:
            print(f"⚠️ 캐시 저장 실패: {e}")
    
    # ==========================================
    # Public API
    # ==========================================
    
    def get_code(self, name: str) -> Optional[str]:
        """
        종목명 → 종목코드
        
        Args:
            name: 종목명 (예: "삼성전자")
            
        Returns:
            종목코드 또는 None
        """
        # 정확히 매칭
        if name in self._name_to_code:
            return self._name_to_code[name]
        
        # 부분 매칭 (첫 번째 결과)
        for stock_name, code in self._name_to_code.items():
            if name in stock_name or stock_name in name:
                return code
        
        return None
    
    def get_name(self, code: str) -> Optional[str]:
        """
        종목코드 → 종목명
        
        Args:
            code: 종목코드 (예: "005930")
            
        Returns:
            종목명 또는 None
        """
        code = code.zfill(6)  # 앞에 0 채우기
        info = self._stocks.get(code)
        return info.name if info else None
    
    def get_info(self, code_or_name: str) -> Optional[StockInfo]:
        """
        종목 정보 조회
        
        Args:
            code_or_name: 종목코드 또는 종목명
            
        Returns:
            StockInfo 또는 None
        """
        # 코드로 시도
        code = code_or_name.zfill(6) if code_or_name.isdigit() else None
        if code and code in self._stocks:
            return self._stocks[code]
        
        # 이름으로 시도
        code = self.get_code(code_or_name)
        if code:
            return self._stocks.get(code)
        
        return None
    
    def search(self, query: str, limit: int = 10) -> List[Dict[str, str]]:
        """
        종목 검색
        
        Args:
            query: 검색어
            limit: 최대 결과 수
            
        Returns:
            [{"name": "...", "code": "...", "market": "..."}, ...]
        """
        results = []
        query_lower = query.lower()
        
        for code, info in self._stocks.items():
            if (query in info.name or 
                query_lower in info.name.lower() or 
                query in code or
                query in info.sector):
                results.append({
                    "name": info.name,
                    "code": info.code,
                    "market": info.market,
                    "sector": info.sector,
                })
                if len(results) >= limit:
                    break
        
        return results
    
    def search_in_text(self, text: str) -> List[Dict[str, str]]:
        """
        텍스트에서 종목 추출
        
        Args:
            text: 검색할 텍스트 (예: "삼성전자 주가가 올랐다")
            
        Returns:
            발견된 종목 리스트
        """
        found = []
        found_codes = set()
        
        for name, code in self._name_to_code.items():
            if name in text and code not in found_codes:
                info = self._stocks.get(code)
                found.append({
                    "name": name,
                    "code": code,
                    "market": info.market if info else "",
                })
                found_codes.add(code)
        
        return found
    
    def get_by_market(self, market: str) -> List[StockInfo]:
        """시장별 종목 조회"""
        return [info for info in self._stocks.values() if info.market == market]
    
    def get_by_sector(self, sector: str) -> List[StockInfo]:
        """업종별 종목 조회"""
        return [info for info in self._stocks.values() if sector in info.sector]
    
    def refresh(self):
        """종목 데이터 강제 갱신"""
        self._stocks.clear()
        self._name_to_code.clear()
        
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
        
        self._load_stocks()
    
    @property
    def count(self) -> int:
        """총 종목 수"""
        return len(self._stocks)


# ==========================================
# 편의 함수 (모듈 레벨)
# ==========================================
_mapper: Optional[StockMapper] = None

def get_mapper() -> StockMapper:
    """싱글톤 매퍼 반환"""
    global _mapper
    if _mapper is None:
        _mapper = StockMapper()
    return _mapper

def get_stock_code(name: str) -> Optional[str]:
    """종목명 → 코드"""
    return get_mapper().get_code(name)

def get_stock_name(code: str) -> Optional[str]:
    """코드 → 종목명"""
    return get_mapper().get_name(code)

def search_stocks(query: str) -> List[Dict[str, str]]:
    """종목 검색"""
    return get_mapper().search(query)

def find_stocks_in_text(text: str) -> List[Dict[str, str]]:
    """텍스트에서 종목 추출"""
    return get_mapper().search_in_text(text)


# ==========================================
# CLI 테스트
# ==========================================
if __name__ == "__main__":
    mapper = StockMapper()
    
    print(f"\n총 종목 수: {mapper.count}개")
    
    # 테스트
    print("\n--- 종목명 → 코드 ---")
    print(f"삼성전자: {mapper.get_code('삼성전자')}")
    print(f"SK하이닉스: {mapper.get_code('SK하이닉스')}")
    print(f"네이버: {mapper.get_code('네이버')}")
    
    print("\n--- 코드 → 종목명 ---")
    print(f"005930: {mapper.get_name('005930')}")
    print(f"000660: {mapper.get_name('000660')}")
    
    print("\n--- 검색 ---")
    results = mapper.search("삼성")
    for r in results[:5]:
        print(f"  {r['name']} ({r['code']}) - {r['market']}")
    
    print("\n--- 텍스트에서 종목 추출 ---")
    text = "삼성전자와 SK하이닉스가 반도체 시장을 주도하고 있다."
    found = mapper.search_in_text(text)
    for f in found:
        print(f"  {f['name']} ({f['code']})")
