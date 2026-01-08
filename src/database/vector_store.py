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
        new_documents = []
        
        # [추가] 중복 방지 로직
        for report in reports:
            # 1. DB에 이미 존재하는지 확인 (Source 링크 기준)
            # ChromaDB의 get 기능을 사용하여 메타데이터로 조회
            existing_docs = self.vector_store.get(
                where={"source": report['link']}
            )
            
            # 2. 이미 있으면 건너뛰기
            if existing_docs and len(existing_docs['ids']) > 0:
                print(f"   (중복) 이미 저장된 리포트: {report['title']}")
                continue

            # 3. 없으면 저장 리스트에 추가
            content = f"[{report['date']}] {report['title']} - {report['broker']}"
            metadata = {
                "stock_code": stock_code,
                "date": report['date'],
                "source": report['link']
            }
            doc = Document(page_content=content, metadata=metadata)
            new_documents.append(doc)
        
        # 4. 새 리포트가 있을 때만 저장
        if new_documents:
            self.vector_store.add_documents(new_documents)
            print(f"💾 ChromaDB에 신규 리포트 {len(new_documents)}건 저장 완료!")
        else:
            print("✨ 저장할 신규 리포트가 없습니다 (모두 중복).")

    def search_similar_reports(self, query, k=3):
        results = self.vector_store.similarity_search(query, k=k)
        return results