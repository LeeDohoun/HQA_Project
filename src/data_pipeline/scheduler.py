# 파일: src/data_pipeline/scheduler.py
"""
데이터 수집 파이프라인 자동화 스케줄러

수집 주기:
- 뉴스: 30분마다 (시장 심리 최신성 유지)
- 주가: 장 마감 후 1회 (18:00)
- 증권사 리포트: 매일 2회 (08:00, 18:00)
- DART 공시: 매일 2회 (09:00, 17:00)

사용법 (CLI):
    # 스케줄러 시작 (데몬 모드)
    python -m src.data_pipeline.scheduler start

    # 특정 종목만 즉시 수집
    python -m src.data_pipeline.scheduler run --codes 005930,000660

    # watchlist 설정 후 스케줄러 시작
    python -m src.data_pipeline.scheduler start --watchlist ./watchlist.json

watchlist.json 예시:
    {
        "stocks": [
            {"code": "005930", "name": "삼성전자"},
            {"code": "000660", "name": "SK하이닉스"}
        ]
    }
"""

import os
import sys
import json
import signal
import logging
import argparse
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 한국 표준시
KST = timezone(timedelta(hours=9))


# ==========================================
# 설정
# ==========================================

@dataclass
class ScheduleConfig:
    """스케줄링 설정"""

    # 수집 주기 (분 단위)
    news_interval_min: int = 30        # 뉴스: 30분
    price_interval_min: int = 1440     # 주가: 1일 (장 마감 후)
    report_interval_min: int = 720     # 리포트: 12시간
    dart_interval_min: int = 720       # 공시: 12시간

    # 시장 시간 (KST)
    market_open_hour: int = 9          # 개장 09:00
    market_close_hour: int = 16        # 마감 15:30 → 16:00 이후에 주가 수집

    # 수집 설정
    news_max_count: int = 10           # 뉴스 최대 수집 건수
    report_max_count: int = 5          # 리포트 최대 수집 건수
    dart_max_count: int = 10           # 공시 최대 수집 건수
    price_days: int = 300              # 주가 데이터 일수

    # 자동 RAG 임베딩
    auto_embed: bool = True

    # 재시도
    max_retries: int = 3
    retry_delay_sec: int = 60          # 실패 시 60초 후 재시도


@dataclass
class StockTarget:
    """수집 대상 종목"""
    code: str
    name: str


@dataclass
class CollectionResult:
    """수집 결과"""
    stock_code: str
    stock_name: str
    task: str              # "news" | "report" | "dart" | "price"
    timestamp: str
    collected: int = 0
    new_items: int = 0
    embedded: int = 0
    success: bool = True
    error: Optional[str] = None


# ==========================================
# Watchlist 관리
# ==========================================

class Watchlist:
    """수집 대상 종목 관리"""

    DEFAULT_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "data", "watchlist.json"
    )

    def __init__(self, path: Optional[str] = None):
        self.path = path or self.DEFAULT_PATH
        self._stocks: List[StockTarget] = []
        self._load()

    def _load(self):
        """파일에서 종목 목록 로드"""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._stocks = [
                    StockTarget(code=s["code"], name=s["name"])
                    for s in data.get("stocks", [])
                ]
                logger.info(f"📋 Watchlist 로드: {len(self._stocks)}개 종목 ({self.path})")
            except Exception as e:
                logger.error(f"Watchlist 로드 실패: {e}")
                self._stocks = []
        else:
            logger.info(f"📋 Watchlist 파일 없음 ({self.path}), 기본 종목 사용")
            self._stocks = self._default_stocks()
            self.save()  # 기본 파일 생성

    def _default_stocks(self) -> List[StockTarget]:
        """기본 watchlist (예시 종목)"""
        return [
            StockTarget("005930", "삼성전자"),
            StockTarget("000660", "SK하이닉스"),
            StockTarget("035420", "NAVER"),
        ]

    def save(self):
        """파일에 저장"""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            "stocks": [{"code": s.code, "name": s.name} for s in self._stocks],
            "updated_at": datetime.now(KST).isoformat(),
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Watchlist 저장: {len(self._stocks)}개 종목")

    @property
    def stocks(self) -> List[StockTarget]:
        return list(self._stocks)

    def add(self, code: str, name: str):
        """종목 추가"""
        if not any(s.code == code for s in self._stocks):
            self._stocks.append(StockTarget(code, name))
            self.save()
            logger.info(f"➕ Watchlist 추가: {name}({code})")

    def remove(self, code: str):
        """종목 제거"""
        self._stocks = [s for s in self._stocks if s.code != code]
        self.save()
        logger.info(f"➖ Watchlist 제거: {code}")


# ==========================================
# 수집 작업 (Task)
# ==========================================

class CollectionTask:
    """
    개별 수집 작업

    DataIngestionPipeline의 개별 수집 함수를 래핑하여
    에러 처리, 재시도, 로깅을 추가합니다.
    """

    def __init__(self, config: ScheduleConfig):
        self.config = config
        self._pipeline = None  # lazy init

    @property
    def pipeline(self):
        """파이프라인 lazy 초기화 (무거운 모듈 지연 로드)"""
        if self._pipeline is None:
            from src.data_pipeline.data_ingestion import DataIngestionPipeline
            self._pipeline = DataIngestionPipeline(
                use_reranker=True,
                retrieval_k=20,
                rerank_top_k=3,
            )
        return self._pipeline

    def collect_news(self, stock: StockTarget) -> CollectionResult:
        """뉴스 수집"""
        return self._run_task(stock, "news", lambda: (
            self.pipeline._collect_news(stock.code, stock.name)
        ))

    def collect_reports(self, stock: StockTarget) -> CollectionResult:
        """증권사 리포트 수집"""
        return self._run_task(stock, "report", lambda: (
            self.pipeline._collect_reports(stock.code, stock.name)
        ))

    def collect_dart(self, stock: StockTarget) -> CollectionResult:
        """DART 공시 수집"""
        return self._run_task(stock, "dart", lambda: (
            self.pipeline._collect_disclosures(stock.code, stock.name)
        ))

    def collect_price(self, stock: StockTarget) -> CollectionResult:
        """주가 데이터 수집"""
        timestamp = datetime.now(KST).isoformat()
        try:
            count = self.pipeline._collect_price(stock.code, stock.name)
            return CollectionResult(
                stock_code=stock.code,
                stock_name=stock.name,
                task="price",
                timestamp=timestamp,
                collected=count,
            )
        except Exception as e:
            logger.exception(f"주가 수집 실패: {stock.name}")
            return CollectionResult(
                stock_code=stock.code,
                stock_name=stock.name,
                task="price",
                timestamp=timestamp,
                success=False,
                error=str(e),
            )

    def embed_pending(self, stock: StockTarget) -> Dict:
        """미처리 데이터 RAG 임베딩"""
        try:
            return self.pipeline.embed_pending_data(stock.code)
        except Exception as e:
            logger.exception(f"임베딩 실패: {stock.name}")
            return {"error": str(e)}

    def _run_task(self, stock: StockTarget, task_name: str, fn) -> CollectionResult:
        """공통 실행 래퍼 (에러 처리 + 재시도)"""
        timestamp = datetime.now(KST).isoformat()

        for attempt in range(1, self.config.max_retries + 1):
            try:
                collected, new_items = fn()
                result = CollectionResult(
                    stock_code=stock.code,
                    stock_name=stock.name,
                    task=task_name,
                    timestamp=timestamp,
                    collected=collected,
                    new_items=new_items,
                )

                # 자동 임베딩
                if self.config.auto_embed and new_items > 0:
                    embedded = self.embed_pending(stock)
                    result.embedded = sum(embedded.get(k, 0) for k in ["reports", "news", "disclosures"])

                return result

            except Exception as e:
                logger.warning(
                    f"⚠️ [{stock.name}] {task_name} 수집 실패 "
                    f"(시도 {attempt}/{self.config.max_retries}): {e}"
                )
                if attempt < self.config.max_retries:
                    import time
                    time.sleep(self.config.retry_delay_sec)

        return CollectionResult(
            stock_code=stock.code,
            stock_name=stock.name,
            task=task_name,
            timestamp=timestamp,
            success=False,
            error=f"{self.config.max_retries}회 재시도 모두 실패",
        )


# ==========================================
# 스케줄러 엔진
# ==========================================

class PipelineScheduler:
    """
    데이터 수집 파이프라인 스케줄러

    각 수집 작업(뉴스/리포트/공시/주가)을 정해진 주기로 실행합니다.
    시장 시간을 고려하여 불필요한 수집을 스킵합니다.

    사용법:
        scheduler = PipelineScheduler()
        scheduler.start()   # 블로킹 (Ctrl+C로 종료)
    """

    def __init__(
        self,
        config: Optional[ScheduleConfig] = None,
        watchlist_path: Optional[str] = None,
    ):
        self.config = config or ScheduleConfig()
        self.watchlist = Watchlist(watchlist_path)
        self.task = CollectionTask(self.config)

        # 마지막 실행 시각 추적
        self._last_run: Dict[str, datetime] = {
            "news": datetime.min.replace(tzinfo=KST),
            "report": datetime.min.replace(tzinfo=KST),
            "dart": datetime.min.replace(tzinfo=KST),
            "price": datetime.min.replace(tzinfo=KST),
        }

        # 수집 이력
        self._history: List[CollectionResult] = []
        self._history_max = 1000

        # 종료 플래그
        self._running = False

    def start(self):
        """
        스케줄러 시작 (블로킹)

        Ctrl+C(SIGINT) 또는 SIGTERM으로 종료합니다.
        """
        self._running = True

        # 시그널 핸들러 등록
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        logger.info("=" * 60)
        logger.info("🚀 데이터 수집 스케줄러 시작")
        logger.info(f"   종목: {len(self.watchlist.stocks)}개")
        logger.info(f"   뉴스: {self.config.news_interval_min}분 주기")
        logger.info(f"   리포트: {self.config.report_interval_min}분 주기")
        logger.info(f"   공시: {self.config.dart_interval_min}분 주기")
        logger.info(f"   주가: 장 마감 후 (16:00 이후)")
        logger.info(f"   자동 임베딩: {'ON' if self.config.auto_embed else 'OFF'}")
        logger.info("=" * 60)

        print("\n🚀 데이터 수집 스케줄러 시작!")
        print(f"   📋 종목: {', '.join(s.name for s in self.watchlist.stocks)}")
        print(f"   📰 뉴스: {self.config.news_interval_min}분 주기")
        print(f"   📑 리포트: {self.config.report_interval_min // 60}시간 주기")
        print(f"   📋 공시: {self.config.dart_interval_min // 60}시간 주기")
        print(f"   📈 주가: 장 마감 후 1회")
        print(f"   🧠 자동 RAG 임베딩: {'ON' if self.config.auto_embed else 'OFF'}")
        print("   ⏹️  종료: Ctrl+C\n")

        # 시작 시 즉시 1회 수집
        self._run_all_tasks()

        # 메인 루프: 1분마다 체크
        import time
        while self._running:
            try:
                time.sleep(60)  # 1분 대기
                self._check_and_run()
            except Exception as e:
                logger.exception(f"스케줄러 루프 오류: {e}")
                time.sleep(10)

        logger.info("🛑 스케줄러 종료")
        print("\n🛑 스케줄러가 안전하게 종료되었습니다.")

    def stop(self):
        """스케줄러 중지"""
        self._running = False

    def _handle_signal(self, signum, frame):
        """시그널 핸들러"""
        logger.info(f"시그널 수신 ({signum}), 종료 중...")
        self.stop()

    # ──────────────────────────────────────────────
    # 주기 확인 및 실행
    # ──────────────────────────────────────────────

    def _check_and_run(self):
        """주기를 확인하고 실행할 작업 결정"""
        now = datetime.now(KST)

        # 뉴스: 항상 (주기에 따라)
        if self._should_run("news", self.config.news_interval_min, now):
            self._run_for_all("news", now)

        # 리포트: 주기에 따라
        if self._should_run("report", self.config.report_interval_min, now):
            self._run_for_all("report", now)

        # 공시: 주기에 따라
        if self._should_run("dart", self.config.dart_interval_min, now):
            self._run_for_all("dart", now)

        # 주가: 장 마감 후 (16:00~23:59) + 오늘 아직 안 했으면
        if self._should_run_price(now):
            self._run_for_all("price", now)

    def _should_run(self, task_name: str, interval_min: int, now: datetime) -> bool:
        """주기 경과 여부 확인"""
        last = self._last_run.get(task_name, datetime.min.replace(tzinfo=KST))
        elapsed = (now - last).total_seconds() / 60
        return elapsed >= interval_min

    def _should_run_price(self, now: datetime) -> bool:
        """주가 수집 시점 판단 (장 마감 후 + 오늘 안 했으면)"""
        if now.hour < self.config.market_close_hour:
            return False  # 장 마감 전이면 스킵

        if now.weekday() >= 5:
            return False  # 주말이면 스킵

        last = self._last_run.get("price", datetime.min.replace(tzinfo=KST))
        return last.date() < now.date()  # 오늘 아직 수집 안 했으면

    # ──────────────────────────────────────────────
    # 수집 실행
    # ──────────────────────────────────────────────

    def _run_for_all(self, task_name: str, now: datetime):
        """모든 종목에 대해 특정 작업 실행"""
        stocks = self.watchlist.stocks
        if not stocks:
            return

        task_emoji = {"news": "📰", "report": "📑", "dart": "📋", "price": "📈"}
        emoji = task_emoji.get(task_name, "▶️")

        logger.info(f"{emoji} [{task_name.upper()}] 수집 시작 ({len(stocks)}개 종목)")
        print(f"\n{emoji} [{task_name.upper()}] {now.strftime('%H:%M')} — "
              f"{len(stocks)}개 종목 수집 시작")

        for stock in stocks:
            if not self._running:
                break

            result = self._execute_task(stock, task_name)
            self._record_result(result)

            # 크롤링 과부하 방지 (2초 간격)
            import time
            time.sleep(2)

        self._last_run[task_name] = now
        logger.info(f"{emoji} [{task_name.upper()}] 수집 완료")

    def _execute_task(self, stock: StockTarget, task_name: str) -> CollectionResult:
        """작업 실행 (디스패치)"""
        dispatch = {
            "news": self.task.collect_news,
            "report": self.task.collect_reports,
            "dart": self.task.collect_dart,
            "price": self.task.collect_price,
        }

        fn = dispatch.get(task_name)
        if fn is None:
            return CollectionResult(
                stock_code=stock.code,
                stock_name=stock.name,
                task=task_name,
                timestamp=datetime.now(KST).isoformat(),
                success=False,
                error=f"알 수 없는 작업: {task_name}",
            )

        result = fn(stock)

        # 로그 출력
        status = "✅" if result.success else "❌"
        detail = f"수집 {result.collected}, 신규 {result.new_items}"
        if result.embedded:
            detail += f", 임베딩 {result.embedded}"
        if result.error:
            detail = f"오류: {result.error}"

        print(f"   {status} [{stock.name}] {task_name}: {detail}")
        return result

    def _run_all_tasks(self):
        """모든 작업 즉시 실행 (시작 시 1회)"""
        now = datetime.now(KST)
        print(f"\n🔄 초기 수집 시작 ({now.strftime('%Y-%m-%d %H:%M')})")

        for task_name in ["news", "report", "dart"]:
            self._run_for_all(task_name, now)

        # 주가는 장 마감 후만
        if self._should_run_price(now):
            self._run_for_all("price", now)
        else:
            print(f"\n📈 [PRICE] 장 마감 전 — 주가 수집은 {self.config.market_close_hour}:00 이후에 실행")

    # ──────────────────────────────────────────────
    # 수집 이력
    # ──────────────────────────────────────────────

    def _record_result(self, result: CollectionResult):
        """수집 결과 기록"""
        self._history.append(result)
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max:]

    def get_history(self, limit: int = 50) -> List[CollectionResult]:
        """최근 수집 이력 조회"""
        return list(reversed(self._history[-limit:]))

    def get_stats(self) -> Dict:
        """수집 통계"""
        now = datetime.now(KST)

        stats = {
            "scheduler_running": self._running,
            "watchlist_count": len(self.watchlist.stocks),
            "total_runs": len(self._history),
            "last_run": {},
        }

        for task_name, last_time in self._last_run.items():
            if last_time.year > 2000:
                stats["last_run"][task_name] = {
                    "timestamp": last_time.isoformat(),
                    "minutes_ago": int((now - last_time).total_seconds() / 60),
                }

        # 성공/실패 카운트
        recent = self._history[-100:] if self._history else []
        stats["recent_success"] = sum(1 for r in recent if r.success)
        stats["recent_failed"] = sum(1 for r in recent if not r.success)

        return stats


# ==========================================
# 즉시 실행 (1회성 수집)
# ==========================================

def run_once(
    codes: List[str],
    names: Optional[List[str]] = None,
    tasks: Optional[List[str]] = None,
):
    """
    지정 종목의 데이터를 1회 수집

    Args:
        codes: 종목코드 리스트
        names: 종목명 리스트 (없으면 코드를 이름으로 사용)
        tasks: 수집 작업 리스트 (기본: 전체)
    """
    if not tasks:
        tasks = ["news", "report", "dart", "price"]

    if not names:
        # 종목명 자동 매핑
        try:
            from src.utils.stock_mapper import get_mapper
            mapper = get_mapper()
            names = [mapper.code_to_name(c) or c for c in codes]
        except Exception:
            names = codes

    config = ScheduleConfig(auto_embed=True)
    collector = CollectionTask(config)

    print(f"\n{'=' * 60}")
    print(f"🔄 1회 수집 시작")
    print(f"   종목: {', '.join(names)}")
    print(f"   작업: {', '.join(tasks)}")
    print(f"{'=' * 60}")

    results = []

    for code, name in zip(codes, names):
        stock = StockTarget(code, name)
        print(f"\n📊 [{name}({code})]")

        for task_name in tasks:
            dispatch = {
                "news": collector.collect_news,
                "report": collector.collect_reports,
                "dart": collector.collect_dart,
                "price": collector.collect_price,
            }

            fn = dispatch.get(task_name)
            if fn:
                result = fn(stock)
                results.append(result)

                status = "✅" if result.success else "❌"
                detail = f"수집 {result.collected}, 신규 {result.new_items}"
                if result.error:
                    detail = f"오류: {result.error}"
                print(f"   {status} {task_name}: {detail}")

            import time
            time.sleep(1)

    # 요약
    success = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    total_new = sum(r.new_items for r in results)

    print(f"\n{'=' * 60}")
    print(f"✅ 수집 완료: 성공 {success}, 실패 {failed}, 신규 {total_new}건")
    print(f"{'=' * 60}")

    return results


# ==========================================
# CLI 엔트리포인트
# ==========================================

def main():
    """CLI 엔트리포인트"""
    parser = argparse.ArgumentParser(
        description="HQA 데이터 수집 파이프라인 스케줄러",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 스케줄러 데몬 시작
  python -m src.data_pipeline.scheduler start

  # watchlist 지정하여 시작
  python -m src.data_pipeline.scheduler start --watchlist ./my_stocks.json

  # 특정 종목 1회 수집
  python -m src.data_pipeline.scheduler run --codes 005930,000660

  # 뉴스만 수집
  python -m src.data_pipeline.scheduler run --codes 005930 --tasks news

  # 수집 주기 변경
  python -m src.data_pipeline.scheduler start --news-interval 15
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="명령어")

    # start: 스케줄러 데몬 시작
    start_parser = subparsers.add_parser("start", help="스케줄러 시작 (데몬)")
    start_parser.add_argument("--watchlist", type=str, help="watchlist JSON 파일 경로")
    start_parser.add_argument("--news-interval", type=int, default=30, help="뉴스 수집 주기 (분)")
    start_parser.add_argument("--report-interval", type=int, default=720, help="리포트 수집 주기 (분)")
    start_parser.add_argument("--no-embed", action="store_true", help="자동 RAG 임베딩 비활성화")

    # run: 1회 수집
    run_parser = subparsers.add_parser("run", help="1회 수집 실행")
    run_parser.add_argument("--codes", type=str, required=True, help="종목코드 (콤마 구분)")
    run_parser.add_argument("--tasks", type=str, default="news,report,dart,price",
                           help="수집 작업 (콤마 구분: news,report,dart,price)")

    args = parser.parse_args()

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.command == "start":
        config = ScheduleConfig(
            news_interval_min=args.news_interval,
            report_interval_min=args.report_interval,
            auto_embed=not args.no_embed,
        )
        scheduler = PipelineScheduler(
            config=config,
            watchlist_path=args.watchlist,
        )
        scheduler.start()

    elif args.command == "run":
        codes = [c.strip() for c in args.codes.split(",")]
        tasks = [t.strip() for t in args.tasks.split(",")]
        run_once(codes=codes, tasks=tasks)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
