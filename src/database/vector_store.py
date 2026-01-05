# 파일: src/database/vector_store.py

import os
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from dotenv import load_dotenv

# .env 파일에서 API 키 로드
load_dotenv()

class ReportVectorStore:
    def __init__(self):
        # 1. 임베딩 모델 설정 (OpenAI 사용)
        # 비용 절약을 위해 가장 저렴한 'text-embedding-3-small' 모델 사용 추천
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY가 .env 파일에 없습니다!")
            
        self.embedding_model = OpenAIEmbeddings(model="text-embedding-3-small")
        
        # 2. ChromaDB 저장소 설정 (로컬 폴더에 저장)
        self.persist_dir = "./database/chroma_db"
        self.vector_store = Chroma(
            collection_name="stock_reports",
            embedding_function=self.embedding_model,
            persist_directory=self.persist_dir
        )

    def save_reports(self, reports, stock_code):
        """
        수집된 리포트 리스트를 벡터 DB에 저장합니다.
        """
        documents = []
        for report in reports:
            # 검색에 활용할 텍스트 (제목 + 증권사)
            content = f"[{report['date']}] {report['title']} - {report['broker']}"
            
            # 메타데이터 (나중에 필터링할 때 사용)
            metadata = {
                "stock_code": stock_code,
                "date": report['date'],
                "source": report['link']
            }
            
            # 문서 객체 생성
            doc = Document(page_content=content, metadata=metadata)
            documents.append(doc)
        
        if documents:
            # DB에 추가
            self.vector_store.add_documents(documents)
            print(f"💾 ChromaDB에 {len(documents)}건 저장 완료!")
        else:
            print("⚠️ 저장할 데이터가 없습니다.")

    def search_similar_reports(self, query, k=3):
        """
        질문(query)과 가장 유사한 리포트를 검색합니다.
        """
        results = self.vector_store.similarity_search(query, k=k)
        return results