# 파일: src/agents/llm_config.py
"""
LLM 설정 모듈

모델 구분:
- Instruct (빠름): 정보 수집, 요약, 패턴 인식
- Thinking (깊은 추론): 헤게모니 판단, 최종 결정
- Vision (이미지): 차트/그래프 분석

에이전트별 모델:
- Researcher: Instruct + Vision (정보 수집)
- Strategist: Thinking (헤게모니 판단)
- Quant: Instruct (재무 분석)
- Chartist: Instruct (기술 분석)
- Risk Manager: Thinking (최종 판단)
"""

import os
import base64
from typing import List, Dict, Optional, Union
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

load_dotenv()


def get_api_key() -> str:
    """API 키 로드"""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY가 .env 파일에 없습니다.")
    return api_key


def get_gemini_llm():
    """
    일반 텍스트 분석용 Gemini LLM (Instruct)
    - Researcher, Quant, Chartist용
    - 빠르고 저렴
    """
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite", 
        verbose=True,
        temperature=0.3,
        google_api_key=get_api_key()
    )
    return llm


def get_thinking_llm():
    """
    깊은 추론용 Thinking LLM
    - Strategist, Risk Manager용
    - 복잡한 맥락 추론, 트레이드오프 판단
    """
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-preview-05-20",  # Thinking 지원 모델
        verbose=True,
        temperature=0.5,  # 약간의 창의성 허용
        google_api_key=get_api_key()
    )
    return llm


def get_gemini_vision_llm():
    """
    이미지 분석용 Gemini Vision LLM
    - Researcher의 차트/그래프 분석에 사용
    - 멀티모달 지원
    """
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",  # Vision 지원 모델
        verbose=True,
        temperature=0.3,
        google_api_key=get_api_key()
    )
    return llm


class GeminiVisionAnalyzer:
    """
    Gemini Vision을 사용한 이미지 분석기
    - 증권 리포트의 차트/그래프 분석
    - Base64 이미지 입력 지원
    """
    
    def __init__(self):
        self.llm = get_gemini_vision_llm()
    
    def analyze_image(
        self,
        image_base64: str,
        prompt: str = "이 이미지를 분석해주세요.",
        mime_type: str = "image/png"
    ) -> str:
        """
        단일 이미지 분석
        
        Args:
            image_base64: Base64 인코딩된 이미지
            prompt: 분석 프롬프트
            mime_type: 이미지 MIME 타입
            
        Returns:
            분석 결과 텍스트
        """
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
                }
            ]
        )
        
        response = self.llm.invoke([message])
        return response.content
    
    def analyze_multiple_images(
        self,
        images: List[Dict],
        prompt: str = "다음 이미지들을 분석해주세요."
    ) -> str:
        """
        여러 이미지 동시 분석
        
        Args:
            images: [{"base64": str, "mime_type": str}, ...]
            prompt: 분석 프롬프트
            
        Returns:
            통합 분석 결과
        """
        content = [{"type": "text", "text": prompt}]
        
        for img in images:
            mime_type = img.get("mime_type", "image/png")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{img['base64']}"}
            })
        
        message = HumanMessage(content=content)
        response = self.llm.invoke([message])
        return response.content
    
    def analyze_report_images(
        self,
        image_data_list: List[Dict],
        stock_name: str
    ) -> str:
        """
        증권 리포트 이미지 분석 (RAG 결과용)
        
        Args:
            image_data_list: RAG에서 반환된 이미지 데이터 리스트
                [{"image_base64": str, "source": str, "page_num": int, "text_fallback": str}, ...]
            stock_name: 분석 대상 종목명
            
        Returns:
            차트/그래프 분석 결과
        """
        if not image_data_list:
            return "분석할 이미지가 없습니다."
        
        # 유효한 이미지만 필터링
        valid_images = [
            img for img in image_data_list 
            if img.get("image_base64")
        ]
        
        if not valid_images:
            # 이미지가 없으면 텍스트 fallback 사용
            fallback_texts = [
                f"[{img.get('source', 'unknown')} - 페이지 {img.get('page_num', '?')}]\n{img.get('text_fallback', '')}"
                for img in image_data_list
            ]
            return "이미지 데이터 없음. 텍스트 정보:\n" + "\n\n".join(fallback_texts)
        
        # 분석 프롬프트 구성
        prompt = f"""
당신은 증권 애널리스트입니다. 다음은 '{stock_name}'에 대한 증권사 리포트의 차트/그래프 이미지입니다.

각 이미지를 분석하고 다음 정보를 추출해주세요:

1. **차트 유형**: (매출 추이, 주가 차트, 시장점유율, 실적 전망 등)
2. **핵심 데이터 포인트**: (숫자, 비율, 성장률 등)
3. **트렌드 분석**: (상승/하락/보합, 변곡점)
4. **투자 시사점**: (긍정적/부정적 신호)

분석 결과를 체계적으로 정리해주세요. 한글로 작성하세요.
"""
        
        # 이미지 컨텐츠 구성
        content = [{"type": "text", "text": prompt}]
        
        for i, img in enumerate(valid_images[:5]):  # 최대 5개 이미지
            source = img.get("source", "unknown")
            page_num = img.get("page_num", "?")
            
            # 이미지 정보 추가
            content.append({
                "type": "text",
                "text": f"\n[이미지 {i+1}] 출처: {source}, 페이지: {page_num}"
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img['image_base64']}"}
            })
        
        message = HumanMessage(content=content)
        
        try:
            response = self.llm.invoke([message])
            return response.content
        except Exception as e:
            return f"이미지 분석 중 오류 발생: {str(e)}"