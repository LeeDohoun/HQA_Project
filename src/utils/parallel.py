# 파일: src/utils/parallel.py
"""
병렬 실행 유틸리티

독립적인 에이전트 태스크를 동시에 실행하여 전체 분석 시간을 단축합니다.

사용 예:
    from src.utils.parallel import run_agents_parallel

    results = run_agents_parallel({
        "analyst": (analyst.full_analysis, (stock_name, stock_code)),
        "quant":   (quant.full_analysis,   (stock_name, stock_code)),
        "chartist":(chartist.full_analysis,(stock_name, stock_code)),
    })
    analyst_score  = results["analyst"]
    quant_score    = results["quant"]
    chartist_score = results["chartist"]
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed


def run_agents_parallel(
    tasks: Dict[str, Tuple[Callable, tuple]],
    max_workers: int | None = None,
    timeout: float | None = 120,
) -> Dict[str, Any]:
    """
    여러 에이전트 함수를 병렬로 실행합니다.

    Args:
        tasks: {이름: (함수, (인자1, 인자2, ...))} 딕셔너리
        max_workers: 최대 동시 스레드 수 (기본: 태스크 수)
        timeout: 전체 제한 시간(초). None이면 무제한.

    Returns:
        {이름: 결과} 딕셔너리.
        개별 태스크가 예외를 던지면 해당 결과는 Exception 객체.

    Example:
        results = run_agents_parallel({
            "quant":    (quant.full_analysis, ("삼성전자", "005930")),
            "chartist": (chartist.full_analysis, ("삼성전자", "005930")),
        })
    """
    if max_workers is None:
        max_workers = len(tasks)

    results: Dict[str, Any] = {}
    start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 제출
        future_to_name = {
            executor.submit(func, *args): name
            for name, (func, args) in tasks.items()
        }

        # 수거
        for future in as_completed(future_to_name, timeout=timeout):
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                print(f"⚠️ [{name}] 에이전트 병렬 실행 오류: {exc}")
                results[name] = exc

    elapsed = time.perf_counter() - start
    print(f"⏱️  병렬 실행 완료: {elapsed:.1f}초 (에이전트 {len(tasks)}개)")
    return results


def is_error(result: Any) -> bool:
    """run_agents_parallel 결과가 오류인지 확인"""
    return isinstance(result, Exception)
