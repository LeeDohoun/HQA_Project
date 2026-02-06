# 파일: src/tools/search_tool.py
"""
RAG 검색 도구
- 텍스트 검색: 기존 리포트 내용 검색
- 멀티모달 검색: 텍스트 + 이미지(차트/그래프) 검색
"""

from typing import List, Dict, Optional
from crewai.tools import BaseTool
from pydantic import Field
from src.database.vector_store import ReportVectorStore, RAGRetriever


class StockReportSearchTool(BaseTool):
    """텍스트 기반 리포트 검색 도구"""
    
    name: str = "Stock Report Search"
    description: str = (
        "Search for latest stock market reports and analysis. "
        "Useful for finding information about specific companies, "
        "market trends, or analyst opinions. "
        "Returns text content from securities company reports."
    )

    def _run(self, query: str) -> str:
        """
        벡터 DB에서 관련 리포트를 검색합니다.
        """
        try:
            db = ReportVectorStore()
            results = db.search_similar_reports(query, k=3)
            
            if not results:
                return "검색 결과가 없습니다. 다른 키워드로 시도해보세요."
            
            output = "\n검색된 리포트 내용:\n"
            for i, doc in enumerate(results, 1):
                source = doc.metadata.get("source", "unknown")
                page = doc.metadata.get("page_num", "?")
                output += f"\n[{i}] (출처: {source}, 페이지: {page})\n"
                output += f"{doc.page_content}\n"
                
            return output
            
        except Exception as e:
            return f"검색 중 오류 발생: {str(e)}"


class MultimodalReportSearchTool(BaseTool):
    """멀티모달 리포트 검색 도구 - 텍스트 + 이미지"""
    
    name: str = "Multimodal Report Search"
    description: str = (
        "Search for stock reports including charts and graphs. "
        "Returns both text content AND image data (base64) from reports. "
        "Use this when you need to analyze visual data like charts, graphs, or tables."
    )

    def _run(self, query: str) -> Dict:
        """
        텍스트와 이미지를 함께 검색합니다.
        
        Returns:
            {
                "text_results": [...],
                "image_data": [{"image_base64": ..., "source": ..., "page_num": ...}, ...],
                "summary": "검색 요약"
            }
        """
        try:
            retriever = RAGRetriever(
                persist_dir="./database/chroma_db",
                collection_name="stock_reports"
            )
            
            # 멀티모달 검색 수행
            result = retriever.retrieve(query, k=3, include_images=True)
            
            # 텍스트 결과 정리
            text_contents = []
            for doc in result.text_results:
                text_contents.append({
                    "content": doc.page_content,
                    "source": doc.metadata.get("source", "unknown"),
                    "page_num": doc.metadata.get("page_num", 0)
                })
            
            # 이미지 데이터 추출
            image_data = retriever.get_image_data(result.image_results)
            
            return {
                "query": query,
                "text_results": text_contents,
                "image_data": image_data,
                "has_images": len(image_data) > 0,
                "summary": f"텍스트 {len(text_contents)}건, 이미지 {len(image_data)}건 검색됨"
            }
            
        except Exception as e:
            return {
                "query": query,
                "text_results": [],
                "image_data": [],
                "has_images": False,
                "error": str(e),
                "summary": f"검색 중 오류: {str(e)}"
            }


class ReportImageSearchTool(BaseTool):
    """리포트 이미지만 검색하는 도구"""
    
    name: str = "Report Image Search"
    description: str = (
        "Search for chart and graph images from stock reports. "
        "Returns image data (base64) for visual analysis. "
        "Use this when you specifically need to analyze charts or graphs."
    )

    def _run(self, query: str) -> Dict:
        """
        이미지만 검색합니다.
        """
        try:
            retriever = RAGRetriever(
                persist_dir="./database/chroma_db",
                collection_name="stock_reports"
            )
            
            # 이미지 검색
            image_results = retriever.vector_store.search_images(query, k=5)
            
            # 이미지 데이터 추출
            image_data = retriever.get_image_data(image_results)
            
            if not image_data:
                return {
                    "query": query,
                    "images": [],
                    "count": 0,
                    "message": "관련 이미지를 찾을 수 없습니다."
                }
            
            return {
                "query": query,
                "images": image_data,
                "count": len(image_data),
                "message": f"{len(image_data)}개의 이미지를 찾았습니다."
            }
            
        except Exception as e:
            return {
                "query": query,
                "images": [],
                "count": 0,
                "error": str(e)
            }


# CrewAI 도구 인스턴스
stock_report_search_tool = StockReportSearchTool()
multimodal_search_tool = MultimodalReportSearchTool()
image_search_tool = ReportImageSearchTool()