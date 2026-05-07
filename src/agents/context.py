"""
공통 에이전트 컨텍스트 스키마

에이전트 간 전달용 중간 데이터 구조를 표준화합니다.
- 점수는 라우팅/집계용
- 패킷은 다음 에이전트가 읽는 구조화된 근거용
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List


@dataclass
class EvidenceItem:
    """근거 스니펫 1개"""

    source: str
    title: str = ""
    snippet: str = ""
    url: str = ""
    note: str = ""


@dataclass
class AgentContextPacket:
    """에이전트 간 전달용 구조화 패킷"""

    agent_name: str
    stock_name: str
    stock_code: str

    summary: str = ""
    key_points: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    catalysts: List[str] = field(default_factory=list)
    contrarian_view: str = ""
    evidence: List[EvidenceItem] = field(default_factory=list)

    score: int = 0
    confidence: int = 0
    grade: str = ""
    signal: str = ""
    next_action: str = ""

    source_tags: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_prompt_block(self) -> str:
        """LLM 입력용 마크다운 블록"""
        lines = [
            f"### {self.agent_name}",
            f"- 요약: {self.summary or '없음'}",
            f"- 점수: {self.score}",
            f"- 확신도: {self.confidence}%",
        ]

        if self.grade:
            lines.append(f"- 등급/신호: {self.grade or self.signal}")
        if self.next_action:
            lines.append(f"- 다음 행동: {self.next_action}")
        if self.key_points:
            lines.append("- 핵심 포인트:")
            lines.extend([f"  - {point}" for point in self.key_points])
        if self.catalysts:
            lines.append("- 촉매:")
            lines.extend([f"  - {item}" for item in self.catalysts])
        if self.risks:
            lines.append("- 리스크:")
            lines.extend([f"  - {item}" for item in self.risks])
        if self.contrarian_view:
            lines.append(f"- 반대 의견: {self.contrarian_view}")
        if self.evidence:
            lines.append("- 근거:")
            for item in self.evidence[:5]:
                evidence_line = f"  - [{item.source}] {item.title or item.snippet}"
                if item.snippet and item.title:
                    evidence_line += f" :: {item.snippet}"
                lines.append(evidence_line)
        if self.source_tags:
            lines.append(f"- 소스 태그: {', '.join(self.source_tags)}")

        return "\n".join(lines)

