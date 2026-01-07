# 파일: src/database/vector_store.py

import os
# [핵심 변경] 충돌 나는 langchain_chroma 대신 community 사용
from langchain_community.vectorstores import Chroma 
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
from dotenv import load_dotenv

load_dotenv()

class ReportVectorStore:
    def __init__(self):
        print("⚙️ 로컬 임베딩 모델 로딩 중... (최초 실행 시 다운로드 대기)")
        # 무료 로컬 임베딩 모델 설정
        self.embedding_model = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
        
        # ChromaDB 설정
        self.persist_dir = "./database/chroma_db"
        self.vector_store = Chroma(
            collection_name="stock_reports",
            embedding_function=self.embedding_model,
            persist_directory=self.persist_dir
        )

    def save_reports(self, reports, stock_code):
        documents = []
        for report in reports:
            # 검색 품질을 위해 텍스트 구성
            content = f"[{report['date']}] {report['title']} - {report['broker']}"
            metadata = {
                "stock_code": stock_code,
                "date": report['date'],
                "source": report['link']
            }
            doc = Document(page_content=content, metadata=metadata)
            documents.append(doc)
        
        if documents:
            self.vector_store.add_documents(documents)
            print(f"💾 ChromaDB에 {len(documents)}건 저장 완료!")
        else:
            print("⚠️ 저장할 데이터가 없습니다.")

    def search_similar_reports(self, query, k=3):
        results = self.vector_store.similarity_search(query, k=k)
        return results