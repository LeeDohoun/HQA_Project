# 파일: src/agents/quant.py

from crewai import Agent, Task, Crew, Process
from src.agents.llm_config import get_gemini_llm
from src.tools.finance_tool import FinancialAnalysisTool

class QuantAgent:
    def __init__(self):
        self.llm = get_gemini_llm()

    def analyze_fundamentals(self, stock_name, stock_code):
        # 0. 도구 준비
        finance_tool = FinancialAnalysisTool()

        # 1. 에이전트 정의 (냉철한 펀드 매니저)
        quant = Agent(
            role='Senior Quantitative Fund Manager',
            goal=f'{stock_name}의 재무제표와 밸류에이션 지표를 분석하여 적정 주가 판단',
            backstory="""
                당신은 숫자를 거짓말하지 않는다고 믿는 냉철한 퀀트 투자자입니다.
                기업의 스토리나 뉴스보다는 PER, PBR, ROE 같은 실제 데이터에 기반해 의사결정을 내립니다.
                특히 고평가된 주식을 경계하며, 이익 대비 싼 주식을 찾는 것을 목표로 합니다.
            """,
            tools=[finance_tool],
            llm=self.llm,
            # [🚨핵심 추가] 도구 사용할 때도 Gemini 쓰라고 강제하기
            function_calling_llm=self.llm,
            verbose=True,
            allow_delegation=False
        )

        # 2. 태스크 정의
        # 기획안 퀀트 스코어링 로직 적용
        quant_task = Task(
            description=f"""
                1. '{stock_code}'의 재무 데이터를 도구(Financial Data Search)를 사용해 조회하세요.
                2. 조회된 숫자를 바탕으로 다음 두 가지 핵심 지표를 평가하세요. (주관을 배제하고 숫자로만 판단할 것)
                
                [평가 기준]
                A. 재무 건전성 (Financial Health, 0~20점):
                   - ROE가 높고 꾸준히 이익을 내고 있는가?
                   - 시가총액 대비 실적이 탄탄한가?
                
                B. 밸류에이션 매력도 (Undervaluation, 0~10점):
                   - PER, PBR이 동종 업계나 역사적 평균 대비 낮은가?
                   - 지금 가격이 싸다고 볼 수 있는가?
                
                3. 최종적으로 숫자 중심의 보고서를 작성하세요. (한글 작성)
            """,
            expected_output=f"""
                # {stock_name} 퀀트 분석 보고서
                
                ## 1. 주요 재무 지표
                * 현재가: ...
                * PER: ... / PBR: ... / ROE: ...
                
                ## 2. 핵심 지표 평가
                * **재무 건전성 점수:** XX / 20점
                  - 근거: ...
                * **밸류에이션 점수:** XX / 10점
                  - 근거: ...
                
                ## 3. 퀀트 총평 (매수/매도/보류 의견)
            """,
            agent=quant
        )

        # 3. 크루 실행
        crew = Crew(
            agents=[quant],
            tasks=[quant_task],
            process=Process.sequential,
            verbose=True
        )

        result = crew.kickoff()
        return result