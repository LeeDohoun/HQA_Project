from langchain_core.embeddings import Embeddings
from langchain_community.vectorstores import Chroma
from src.core.config import get_settings
from typing import List, Dict, Any

settings = get_settings()

class ChromaDBManager:
    def __init__(self, embedding_function: Embeddings, collection_name: str = "default"):
        self.embedding_function = embedding_function
        self.collection_name = collection_name
        self.vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embedding_function,
            persist_directory=settings.CHROMA_PERSIST_DIRECTORY
        )
        
    def add_documents(self, documents: List[str], metadatas: List[Dict[str, Any]]):
        """Add new documents to the vector store"""
        self.vectorstore.add_texts(texts=documents, metadatas=metadatas)
        # In recent Chroma versions, persistence is automatic
        
    def similarity_search(self, query: str, k: int = 4, filter: Dict[str, Any] = None):
        """Perform a similarity search with optional metadata filtering"""
        return self.vectorstore.similarity_search(query=query, k=k, filter=filter)
