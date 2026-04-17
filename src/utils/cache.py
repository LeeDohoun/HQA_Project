# 파일: src/utils/cache.py
"""
분석 결과 캐시 (AnalysisCache)

파일 기반 JSON 캐시로 후보 추출, corpus 통계, LLM 분석 결과를
재사용합니다. 외부 의존성(Redis 등) 없이 로컬에서 동작합니다.

특징:
- 파일 기반 JSON 저장 (data/cache/ 하위)
- TTL(Time-To-Live) 기반 만료
- namespace 화이트리스트로 path traversal 차단
- corrupted JSON → None 반환 + 경고 로그
- MetricsCollector에 의존하지 않음 (독립 모듈)

사용 예시:
    cache = AnalysisCache()

    # 기본 get/set
    cache.set("corpus/stats_AI_005930", data, ttl_seconds=3600)
    result = cache.get("corpus/stats_AI_005930")

    # compute 패턴 (hit/miss 정보 포함)
    value, was_cached = cache.get_or_compute(
        "candidate/AI_theme", compute_fn, ttl_seconds=1800
    )

    # 무효화
    cache.invalidate("corpus/stats_AI_005930")
    cache.clear("corpus")
"""

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from src.config.settings import get_data_dir

logger = logging.getLogger(__name__)

# 허용되는 namespace 목록 (path traversal 방지)
_ALLOWED_NAMESPACES = frozenset({"candidate", "corpus", "agent", "chart"})

# 캐시 키에 사용할 수 없는 문자 패턴
_UNSAFE_KEY_PATTERN = re.compile(r"[/\\:*?\"<>|]")


class AnalysisCache:
    """
    파일 기반 분석 결과 캐시

    cache 모듈은 MetricsCollector에 의존하지 않습니다.
    hit/miss 여부는 get_or_compute()의 반환값으로 확인하며,
    호출 측에서 metrics.increment()를 결정합니다.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        """
        Args:
            cache_dir: 캐시 저장 루트 디렉토리 (기본: data/cache)
        """
        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            self._cache_dir = get_data_dir() / "cache"

    def get(self, key: str) -> Optional[Any]:
        """
        캐시 조회

        TTL이 만료된 항목은 None을 반환하고 파일을 삭제합니다.

        Args:
            key: 캐시 키 (예: "corpus/stats_AI_005930_abc123")

        Returns:
            캐시된 값 또는 None
        """
        path = self._key_to_path(key)
        if path is None or not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                envelope = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[AnalysisCache] 손상된 캐시 파일 무시: %s (%s)", path, e)
            self._safe_delete(path)
            return None

        # TTL 확인
        expires_at = envelope.get("expires_at", 0)
        if time.time() > expires_at:
            self._safe_delete(path)
            return None

        return envelope.get("data")

    def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> bool:
        """
        캐시 저장

        Args:
            key: 캐시 키
            value: 저장할 값 (JSON 직렬화 가능한 raw dict/list)
            ttl_seconds: 유효 기간(초)

        Returns:
            저장 성공 여부
        """
        path = self._key_to_path(key)
        if path is None:
            return False

        envelope = {
            "data": value,
            "created_at": time.time(),
            "expires_at": time.time() + ttl_seconds,
            "ttl_seconds": ttl_seconds,
        }

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(envelope, f, ensure_ascii=False, indent=None)
            return True
        except (OSError, TypeError) as e:
            logger.warning("[AnalysisCache] 캐시 저장 실패: %s (%s)", key, e)
            return False

    def get_or_compute(
        self,
        key: str,
        fn: Callable[[], Any],
        ttl_seconds: int = 3600,
    ) -> Tuple[Any, bool]:
        """
        캐시 조회 후 미스 시 fn()을 실행하여 저장

        MetricsCollector에 의존하지 않습니다.
        호출 측에서 was_cached를 보고 metrics.increment()를 결정합니다.

        Args:
            key: 캐시 키
            fn: 캐시 미스 시 실행할 함수
            ttl_seconds: 유효 기간(초)

        Returns:
            (값, 캐시 적중 여부) 튜플
        """
        cached = self.get(key)
        if cached is not None:
            return cached, True

        # 캐시 미스: 계산 실행
        value = fn()
        self.set(key, value, ttl_seconds)
        return value, False

    def invalidate(self, key: str) -> bool:
        """
        단일 키 캐시 삭제

        Args:
            key: 캐시 키

        Returns:
            삭제 성공 여부
        """
        path = self._key_to_path(key)
        if path is None:
            return False
        return self._safe_delete(path)

    def clear(self, namespace: str) -> int:
        """
        네임스페이스별 전체 캐시 삭제

        허용된 namespace만 삭제 가능합니다.
        path traversal 공격을 방지합니다.

        Args:
            namespace: "candidate", "corpus", "agent", "chart" 중 하나

        Returns:
            삭제된 파일 수

        Raises:
            ValueError: 허용되지 않는 namespace
        """
        if namespace not in _ALLOWED_NAMESPACES:
            raise ValueError(
                f"허용되지 않는 namespace: '{namespace}'. "
                f"허용: {sorted(_ALLOWED_NAMESPACES)}"
            )

        ns_dir = self._cache_dir / namespace
        if not ns_dir.exists():
            return 0

        deleted = 0
        for path in ns_dir.glob("*.json"):
            if self._safe_delete(path):
                deleted += 1
        return deleted

    def _key_to_path(self, key: str) -> Optional[Path]:
        """
        캐시 키를 안전한 파일 경로로 변환

        키 형식: "namespace/filename" (예: "corpus/stats_AI_005930_abc123")
        namespace는 화이트리스트에 포함되어야 합니다.
        filename에서 위험 문자는 밑줄로 치환합니다.

        Args:
            key: 캐시 키

        Returns:
            파일 경로 또는 None (유효하지 않은 키)
        """
        if not key or "/" not in key:
            logger.warning("[AnalysisCache] 잘못된 캐시 키 형식: '%s' (namespace/filename 필요)", key)
            return None

        parts = key.split("/", 1)
        namespace = parts[0].strip()
        filename = parts[1].strip()

        # namespace 검증
        if namespace not in _ALLOWED_NAMESPACES:
            logger.warning(
                "[AnalysisCache] 허용되지 않는 namespace: '%s' (허용: %s)",
                namespace, sorted(_ALLOWED_NAMESPACES),
            )
            return None

        # path traversal 차단
        if ".." in filename or filename.startswith("/"):
            logger.warning("[AnalysisCache] path traversal 시도 차단: '%s'", key)
            return None

        # 위험 문자 치환
        safe_filename = _UNSAFE_KEY_PATTERN.sub("_", filename)
        if not safe_filename:
            return None

        # .json 확장자 보장
        if not safe_filename.endswith(".json"):
            safe_filename += ".json"

        return self._cache_dir / namespace / safe_filename

    @staticmethod
    def _safe_delete(path: Path) -> bool:
        """파일 안전 삭제 (존재하지 않으면 무시)"""
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError as e:
            logger.warning("[AnalysisCache] 파일 삭제 실패: %s (%s)", path, e)
            return False


def make_content_hash(path: Path) -> str:
    """
    파일의 mtime + size로 간단한 content hash 생성

    같은 크기·같은 수정시간이면 같은 해시를 반환합니다.
    초기 구현으로 충분하며, 향후 SHA-256 등으로 교체 가능합니다.

    Args:
        path: 파일 경로

    Returns:
        hex 해시 문자열 (16자)
    """
    try:
        stat = path.stat()
        raw = f"{stat.st_mtime_ns}:{stat.st_size}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]
    except OSError:
        return "0" * 16


def make_prompt_hash(prompt: str) -> str:
    """
    프롬프트 텍스트의 SHA-256 해시 (앞 16자)

    Args:
        prompt: 프롬프트 텍스트

    Returns:
        hex 해시 문자열 (16자)
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
