# 파일: src/tools/rag_tool.py

from crewai.tools import BaseTool
from src.database.vector_store import ReportVectorStore

# 전역 변수로 인스턴스 생성 (매번 DB를 로딩하지 않도록)
vector_store_instance = ReportVectorStore()

class RAGSearchTool(BaseTool):
    name: str = "Stock Report Search"
    description: str = (
        "Search specifically for securities company reports regarding a specific stock or issue. "
        "Useful for finding expert analysis, hegemony evaluation, and growth prospects. "
        "Input should be a search query string (e.g., 'Samsung Electronics HBM')."
    )

    def _run(self, query: str) -> str:
        """
        특정 주식이나 이슈에 대한 증권사 리포트 내용을 검색합니다.
        """
        # Phase 1에서 만든 검색 함수 호출
        results = vector_store_instance.search_similar_reports(query, k=3)
        
        if not results:
            return "관련된 리포트를 찾을 수 없습니다."
            
        # 검색 결과를 텍스트 하나로 합쳐서 에이전트에게 전달
        context = "\n\n".join([doc.page_content for doc in results])
        return f"검색된 리포트 내용:\n{context}"