# 파일: src/agents/analyst.py

from crewai import Agent, Task, Crew, Process
from src.agents.llm_config import get_gemini_llm
from src.tools.search_tool import StockReportSearchTool

class AnalystAgent:
    def __init__(self):
        # 1. Gemini 모델 불러오기
        self.llm = get_gemini_llm()

    def analyze_stock(self, stock_name, stock_code):
        # 2. 도구 준비
        search_tool = StockReportSearchTool()

        # 3. 에이전트 설정 (여기가 제일 중요합니다!)
        analyst = Agent(
            role='Senior Equity Analyst',
            goal=f'{stock_name}의 증권사 리포트를 분석하여 시장 지배력(해자)과 성장성을 평가',
            backstory="""
                당신은 여의도 증권가에서 20년 경력을 가진 베테랑 애널리스트입니다.
                단순한 뉴스보다는 증권사 리포트의 텍스트 행간을 읽어내는 능력이 탁월합니다.
                기업의 '경제적 해자(Moat)'와 '장기 성장성'을 중심으로 냉철하게 분석합니다.
            """,
            tools=[search_tool],
            
            # [핵심] 이 줄이 없으면 무조건 OpenAI로 연결하려고 시도합니다!
            llm=self.llm,
            # [🚨핵심 추가] 도구 사용할 때도 Gemini 쓰라고 강제하기
            function_calling_llm=self.llm,
            verbose=True,
            allow_delegation=False,
            # [추가] 속도 조절 (분당 5회 제한)
            max_rpm=5
        )

        # 4. 태스크 설정
        analysis_task = Task(
            description=f"""
                1. '{stock_name}'와 관련된 최신 리포트를 검색 도구(Stock Report Search)를 사용해 찾아보세요.
                2. 검색된 내용을 바탕으로 다음 두 가지 핵심 지표를 평가하세요.
                
                [평가 기준]
                A. 독점력 (Pricing Power, 0~40점):
                   - 시장 점유율이 압도적인가?
                   - 경쟁자가 진입하기 어려운가?
                
                B. 성장성 (Growth, 0~30점):
                   - AI, 로봇 등 미래 산업과 연관되어 있는가?
                   - 매출이 구조적으로 성장하는 구간인가?
                
                3. 최종적으로 보고서를 작성하세요. (반드시 한글로 작성)
            """,
            expected_output=f"""
                # {stock_name} 헤게모니 분석 보고서
                
                ## 1. 리포트 요약
                (검색된 리포트의 핵심 내용을 3줄 요약)
                
                ## 2. 핵심 지표 평가
                * **독점력 점수:** XX / 40점
                   * **이유:** ...
                * **성장성 점수:** XX / 30점
                   * **이유:** ...
                
                ## 3. 총평 (한 줄 요약)
            """,
            agent=analyst
        )

        # 5. 크루 실행
        crew = Crew(
            agents=[analyst],
            tasks=[analysis_task],
            process=Process.sequential,
            verbose=True
        )

        result = crew.kickoff()
        return result