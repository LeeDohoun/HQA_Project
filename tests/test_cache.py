# 파일: tests/test_cache.py
"""
AnalysisCache 단위 테스트

검증 항목:
- get/set 기본 동작
- TTL 만료 처리
- get_or_compute (hit/miss + was_cached 반환값)
- namespace path traversal 차단
- corrupted JSON 파일 복구
- Counter/dict 저장 후 복원 확인
- invalidate / clear
"""

import json
import time
from collections import Counter
from pathlib import Path

import pytest

from src.utils.cache import AnalysisCache, make_content_hash, make_prompt_hash


@pytest.fixture
def cache(tmp_path):
    """임시 디렉토리로 캐시 생성"""
    return AnalysisCache(cache_dir=str(tmp_path))


@pytest.fixture
def cache_dir(tmp_path):
    """캐시 루트 디렉토리"""
    return tmp_path


class TestBasicGetSet:
    """기본 get/set 동작"""

    def test_set_and_get(self, cache):
        data = {"stock_name": "삼성전자", "score": 85}
        cache.set("corpus/test_key", data, ttl_seconds=60)
        result = cache.get("corpus/test_key")
        assert result == data

    def test_get_nonexistent(self, cache):
        assert cache.get("corpus/nonexistent") is None

    def test_set_list(self, cache):
        data = [{"code": "005930"}, {"code": "000660"}]
        cache.set("candidate/list_test", data, ttl_seconds=60)
        assert cache.get("candidate/list_test") == data

    def test_counter_as_dict(self, cache):
        """Counter는 dict()로 변환 후 저장, 읽을 때 dict로 복원"""
        original = Counter({"news": 5, "dart": 3, "forum": 1})
        cache.set("corpus/counter_test", dict(original), ttl_seconds=60)
        restored = cache.get("corpus/counter_test")
        assert restored == {"news": 5, "dart": 3, "forum": 1}
        # dict → Counter로 복원 가능
        assert Counter(restored) == original


class TestTTL:
    """TTL 만료 테스트"""

    def test_expired_returns_none(self, cache):
        cache.set("corpus/ttl_test", {"data": 1}, ttl_seconds=1)
        assert cache.get("corpus/ttl_test") is not None
        time.sleep(1.1)
        assert cache.get("corpus/ttl_test") is None

    def test_not_expired(self, cache):
        cache.set("corpus/ttl_alive", {"data": 1}, ttl_seconds=60)
        assert cache.get("corpus/ttl_alive") is not None


class TestGetOrCompute:
    """get_or_compute 테스트"""

    def test_cache_miss(self, cache):
        """미스 시 fn 실행 + 저장"""
        call_count = [0]

        def compute():
            call_count[0] += 1
            return {"computed": True}

        value, was_cached = cache.get_or_compute("corpus/compute_test", compute, ttl_seconds=60)
        assert value == {"computed": True}
        assert was_cached is False
        assert call_count[0] == 1

    def test_cache_hit(self, cache):
        """히트 시 fn 호출하지 않음"""
        call_count = [0]

        def compute():
            call_count[0] += 1
            return {"computed": True}

        # 1회차: 미스
        cache.get_or_compute("corpus/hit_test", compute, ttl_seconds=60)
        assert call_count[0] == 1

        # 2회차: 히트
        value, was_cached = cache.get_or_compute("corpus/hit_test", compute, ttl_seconds=60)
        assert value == {"computed": True}
        assert was_cached is True
        assert call_count[0] == 1  # fn 재호출 없음

    def test_was_cached_flag(self, cache):
        """was_cached 반환값으로 metrics 연동 가능"""
        _, was_cached_1 = cache.get_or_compute("corpus/flag", lambda: "v1", 60)
        _, was_cached_2 = cache.get_or_compute("corpus/flag", lambda: "v2", 60)
        assert was_cached_1 is False
        assert was_cached_2 is True


class TestNamespaceSafety:
    """namespace path traversal 차단"""

    def test_disallowed_namespace(self, cache):
        """허용되지 않은 namespace → None"""
        assert cache.get("secret/data") is None
        assert cache.set("secret/data", {"x": 1}) is False

    def test_path_traversal_dots(self, cache):
        """../ 포함 키 → None"""
        assert cache.get("corpus/../etc/passwd") is None

    def test_path_traversal_slash(self, cache):
        """절대경로 시작 키 → None"""
        assert cache.get("corpus//root/data") is None

    def test_empty_key(self, cache):
        """빈 키 → None"""
        assert cache.get("") is None

    def test_no_namespace(self, cache):
        """namespace 없는 키 → None"""
        assert cache.get("just_a_key") is None

    def test_clear_disallowed_namespace(self, cache):
        """허용되지 않은 namespace clear → ValueError"""
        with pytest.raises(ValueError, match="허용되지 않는 namespace"):
            cache.clear("../etc")

    def test_clear_empty_namespace(self, cache):
        """빈 namespace clear → ValueError"""
        with pytest.raises(ValueError, match="허용되지 않는 namespace"):
            cache.clear("")


class TestCorruptedFiles:
    """손상된 캐시 파일 처리"""

    def test_corrupted_json(self, cache, cache_dir):
        """잘못된 JSON 파일 → None 반환 + 파일 삭제"""
        # 수동으로 손상된 파일 생성
        ns_dir = cache_dir / "corpus"
        ns_dir.mkdir(parents=True)
        bad_file = ns_dir / "corrupted.json"
        bad_file.write_text("{invalid json", encoding="utf-8")

        result = cache.get("corpus/corrupted")
        assert result is None

    def test_empty_file(self, cache, cache_dir):
        """빈 파일 → None 반환"""
        ns_dir = cache_dir / "corpus"
        ns_dir.mkdir(parents=True)
        empty_file = ns_dir / "empty.json"
        empty_file.write_text("", encoding="utf-8")

        result = cache.get("corpus/empty")
        assert result is None


class TestInvalidateAndClear:
    """invalidate / clear 테스트"""

    def test_invalidate(self, cache):
        cache.set("corpus/inv_test", {"x": 1}, ttl_seconds=60)
        assert cache.get("corpus/inv_test") is not None
        cache.invalidate("corpus/inv_test")
        assert cache.get("corpus/inv_test") is None

    def test_clear_namespace(self, cache):
        cache.set("corpus/a", {"a": 1}, ttl_seconds=60)
        cache.set("corpus/b", {"b": 2}, ttl_seconds=60)
        cache.set("candidate/c", {"c": 3}, ttl_seconds=60)

        deleted = cache.clear("corpus")
        assert deleted == 2
        assert cache.get("corpus/a") is None
        assert cache.get("corpus/b") is None
        # 다른 namespace는 영향 없음
        assert cache.get("candidate/c") is not None

    def test_clear_empty_namespace_dir(self, cache):
        """존재하지 않는 namespace 디렉토리 → 0"""
        assert cache.clear("chart") == 0


class TestHashHelpers:
    """해시 유틸리티 함수 테스트"""

    def test_content_hash_same_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h1 = make_content_hash(f)
        h2 = make_content_hash(f)
        assert h1 == h2
        assert len(h1) == 16

    def test_content_hash_nonexistent(self, tmp_path):
        """존재하지 않는 파일 → 0 문자열"""
        h = make_content_hash(tmp_path / "nope")
        assert h == "0" * 16

    def test_prompt_hash(self):
        h1 = make_prompt_hash("같은 프롬프트")
        h2 = make_prompt_hash("같은 프롬프트")
        h3 = make_prompt_hash("다른 프롬프트")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 16


class TestCacheSchemaConsistency:
    """캐시 hit 결과와 miss 결과의 출력 스키마가 같은지"""

    def test_hit_miss_same_schema(self, cache):
        """get_or_compute hit vs miss 결과가 동일 구조"""
        def compute():
            return {"score": 85, "items": [1, 2, 3]}

        val_miss, _ = cache.get_or_compute("corpus/schema_test", compute, 60)
        val_hit, _ = cache.get_or_compute("corpus/schema_test", compute, 60)

        assert val_miss == val_hit
        assert type(val_miss) == type(val_hit)
