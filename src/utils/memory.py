# 파일: src/utils/memory.py
"""
대화형 메모리 (Contextual Memory)

이전 대화의 맥락을 기억하여 후속 질문에서 비교·참조할 수 있도록 합니다.

기능:
- 최근 N턴의 질문/응답 히스토리 유지
- Supervisor 프롬프트에 주입할 히스토리 문자열 생성
- 이전 분석 결과 캐시 (같은 종목 재분석 방지)

사용 예:
    memory = ConversationMemory(max_turns=10)
    memory.add_turn("삼성전자 분석해줘", "적극 매수 (78점)...")
    
    # 다음 쿼리에 히스토리 포함
    history_prompt = memory.to_prompt()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from collections import OrderedDict


@dataclass
class ConversationTurn:
    """대화 한 턴 (질문 + 응답)"""
    query: str
    response_summary: str
    intent: str = ""
    stocks: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


class ConversationMemory:
    """
    대화 히스토리를 관리하는 메모리 모듈.
    
    - 최근 max_turns 만큼 유지
    - 이전 분석 결과를 캐시하여 비교 질문에 활용
    - Supervisor 프롬프트에 주입할 히스토리 텍스트 생성
    """
    
    def __init__(self, max_turns: int = 10, max_cache: int = 20):
        self.max_turns = max_turns
        self.max_cache = max_cache
        self.turns: List[ConversationTurn] = []
        # 종목별 최근 분석 결과 캐시  {stock_name: {scores..., timestamp}}
        self._analysis_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
    
    # ─── 턴 관리 ───
    
    def add_turn(
        self,
        query: str,
        response_summary: str,
        intent: str = "",
        stocks: List[str] | None = None,
    ) -> None:
        """대화 턴 추가"""
        turn = ConversationTurn(
            query=query,
            response_summary=response_summary,
            intent=intent,
            stocks=stocks or [],
        )
        self.turns.append(turn)
        
        # 최대 턴 수 초과 시 오래된 턴 제거
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]
    
    def clear(self) -> None:
        """히스토리 초기화"""
        self.turns.clear()
        self._analysis_cache.clear()
    
    # ─── 분석 캐시 ───
    
    def cache_analysis(self, stock_name: str, result: Dict[str, Any]) -> None:
        """분석 결과를 캐시에 저장"""
        self._analysis_cache[stock_name] = {
            **result,
            "_cached_at": datetime.now().isoformat(),
        }
        # LRU: 최대 개수 초과 시 가장 오래된 항목 제거
        while len(self._analysis_cache) > self.max_cache:
            self._analysis_cache.popitem(last=False)
    
    def get_cached_analysis(self, stock_name: str) -> Optional[Dict[str, Any]]:
        """캐시된 분석 결과 조회"""
        return self._analysis_cache.get(stock_name)
    
    def get_recent_stocks(self) -> List[str]:
        """최근 분석한 종목 목록"""
        stocks = []
        for turn in reversed(self.turns):
            for s in turn.stocks:
                if s not in stocks:
                    stocks.append(s)
        return stocks[:10]
    
    # ─── 프롬프트 생성 ───
    
    def to_prompt(self, max_chars: int = 2000) -> str:
        """
        Supervisor 프롬프트에 주입할 히스토리 텍스트 생성.
        
        Args:
            max_chars: 최대 문자 수 (프롬프트 길이 제한)
        
        Returns:
            히스토리 프롬프트 문자열. 비어있으면 빈 문자열.
        """
        if not self.turns:
            return ""
        
        lines = ["[이전 대화 히스토리]"]
        
        for i, turn in enumerate(self.turns[-5:], 1):  # 최근 5턴만
            lines.append(f"Turn {i}:")
            lines.append(f"  사용자: {turn.query}")
            # 응답은 너무 길면 잘라서 포함
            summary = turn.response_summary
            if len(summary) > 300:
                summary = summary[:300] + "..."
            lines.append(f"  시스템: {summary}")
            if turn.stocks:
                lines.append(f"  관련 종목: {', '.join(turn.stocks)}")
            lines.append("")
        
        text = "\n".join(lines)
        
        # 최대 문자 수 제한
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (이전 대화 생략)"
        
        return text
    
    def get_context_hint(self, query: str) -> str:
        """
        현재 쿼리에 대한 맥락 힌트 생성.
        
        "그럼 하이닉스는?" 같은 후속 질문에서
        이전 맥락(삼성전자 분석)을 참조하도록 힌트를 줍니다.
        
        Args:
            query: 현재 사용자 쿼리
            
        Returns:
            맥락 힌트 문자열. 필요 없으면 빈 문자열.
        """
        if not self.turns:
            return ""
        
        # 지시어/대명사 패턴 감지
        context_indicators = [
            "그럼", "그러면", "그건", "그거", "거기",
            "비교", "대비", "차이", "마찬가지",
            "역시", "또", "도", "는?", "은?", "어때",
            "이전", "아까",
        ]
        
        needs_context = any(indicator in query for indicator in context_indicators)
        
        if not needs_context:
            return ""
        
        # 가장 최근 턴의 맥락 가져오기
        last_turn = self.turns[-1]
        hint_parts = [
            "※ 맥락 참고: 이전 대화에서"
        ]
        
        if last_turn.stocks:
            hint_parts.append(f" '{', '.join(last_turn.stocks)}'을(를) 분석했습니다.")
        else:
            hint_parts.append(f" '{last_turn.query}'라는 질문이 있었습니다.")
        
        # 이전 분석 결과 요약도 포함
        if last_turn.stocks:
            for stock in last_turn.stocks:
                cached = self.get_cached_analysis(stock)
                if cached:
                    score_info = []
                    if "total_score" in cached:
                        score_info.append(f"종합 {cached['total_score']}점")
                    if "action" in cached:
                        score_info.append(f"판단: {cached['action']}")
                    if score_info:
                        hint_parts.append(f" ({stock}: {', '.join(score_info)})")
        
        hint_parts.append(
            "\n사용자가 비교·후속 질문을 하고 있을 수 있으니, "
            "이전 맥락을 고려하여 응답하세요."
        )
        
        return "".join(hint_parts)
    
    @property
    def turn_count(self) -> int:
        """현재 턴 수"""
        return len(self.turns)
    
    def __repr__(self) -> str:
        return (
            f"ConversationMemory(turns={len(self.turns)}, "
            f"cached_stocks={list(self._analysis_cache.keys())})"
        )
