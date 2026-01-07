# 파일: src/agents/analyst.py

from crewai import Agent, Task, Crew, Process
from src.agents.llm_config import get_gemini_llm
from src.tools.rag_tool import RAGSearchTool # 변경된 클래스 임포트

class AnalystAgent:
    def __init__(self):
        self.llm = get_gemini_llm()

    def analyze_stock(self, stock_name, stock_code):
        # 0. 도구 준비 (여기서 클래스를 인스턴스화 합니다)
        rag_tool = RAGSearchTool()

        # 1. 에이전트 정의 (페르소나)
        analyst = Agent(
            role='Senior Equity Analyst',
            goal=f'{stock_name}의 기업 가치와 헤게모니(독점력)를 심층 분석',
            backstory="""
                당신은 월스트리트에서 20년 경력을 가진 까다로운 주식 분석가입니다.
                단순한 뉴스보다는 기업이 가진 '해자(Moat)'와 '독점력'을 중요하게 생각합니다.
                증권사 리포트의 긍정적인 톤에 속지 않고 비판적으로 분석합니다.
            """,
            tools=[rag_tool], # [변경] 도구 리스트에 인스턴스 추가
            llm=self.llm,
            verbose=True,
            allow_delegation=False
        )

        # 2. 태스크 정의 (해야 할 일)
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
                (검색된 리포트 내용 요약)
                
                ## 2. 핵심 지표 평가
                * **독점력 점수:** XX / 40점
                  - 이유: ...
                * **성장성 점수:** XX / 30점
                  - 이유: ...
                
                ## 3. 총평 (한 줄 요약)
            """,
            agent=analyst
        )

        # 3. 크루 결성 및 실행
        crew = Crew(
            agents=[analyst],
            tasks=[analysis_task],
            process=Process.sequential,
            verbose=True
        )

        result = crew.kickoff()
        return result