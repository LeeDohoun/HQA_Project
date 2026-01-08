# 파일: src/tools/search_tool.py

from crewai.tools import BaseTool
from src.database.vector_store import ReportVectorStore

class StockReportSearchTool(BaseTool):
    name: str = "Stock Report Search"
    description: str = (
        "Search for latest stock market reports and analysis. "
        "Useful for finding information about specific companies, "
        "market trends, or analyst opinions."
    )

    def _run(self, query: str) -> str:
        """
        벡터 DB에서 관련 리포트를 검색합니다.
        """
        try:
            # DB 연결
            db = ReportVectorStore()
            
            # 검색 실행 (상위 3개)
            results = db.search_similar_reports(query, k=3)
            
            if not results:
                return "검색 결과가 없습니다. 다른 키워드로 시도해보세요."
            
            # 검색 결과를 LLM이 읽기 좋게 텍스트로 변환
            output = "\n검색된 리포트 내용:\n"
            for doc in results:
                output += f"- {doc.page_content}\n"
                
            return output
            
        except Exception as e:
            return f"검색 중 오류 발생: {str(e)}"