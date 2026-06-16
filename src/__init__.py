# 파일: src/__init__.py
"""
HQA (Hegemony Quantitative Analyst)
AI 기반 멀티 에이전트 주식 분석 시스템

구조:
- agents: AI 에이전트 (Supervisor, Analyst, Quant, Chartist, RiskManager)
- tools: 분석 도구 (검색, 재무, 시세)
- evidence: 수집 문서 기반 evidence/context 계층
- data_pipeline: 데이터 수집 파이프라인
- database: 데이터 저장소
- utils: 유틸리티 (종목매핑, 인증 등)
"""

__version__ = "1.0.0"
__author__ = "HQA Team"
