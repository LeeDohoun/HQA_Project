# 파일: src/rag/reranker.py
"""
Qwen3 Reranker 모듈
- 검색 결과 재순위 지정
- Query-Document 관련성 점수 계산
"""

import torch
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class RerankResult:
    """리랭킹 결과 데이터 클래스"""
    content: str
    score: float
    original_index: int
    metadata: Dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class Qwen3Reranker:
    """
    Qwen3-Reranker 기반 문서 재순위 클래스
    
    특징:
    - 0.6B/4B/8B 모델 지원
    - 100+ 언어 지원
    - 32K 컨텍스트 길이
    - Instruction-aware 리랭킹
    """
    
    MODELS = {
        "default": "Qwen/Qwen3-Reranker-0.6B",
        "small": "Qwen/Qwen3-Reranker-0.6B",
        "medium": "Qwen/Qwen3-Reranker-4B",
        "large": "Qwen/Qwen3-Reranker-8B",
    }
    
    # 작업별 기본 Instruction (영어로 작성 권장)
    DEFAULT_INSTRUCTIONS = {
        "retrieval": "Given a web search query, retrieve relevant passages that answer the query",
        "qa": "Given a question, retrieve passages that contain the answer to the question",
        "finance": "Given a financial query, retrieve relevant financial documents, reports, or news that answer the query",
        "code": "Given a code-related query, retrieve relevant code snippets or documentation",
        "semantic": "Given a query, retrieve semantically similar passages",
    }
    
    def __init__(
        self,
        model_name: str = "default",
        device: Optional[str] = None,
        max_length: int = 8192,
        use_fp16: bool = True,
        use_flash_attention: bool = False,
    ):
        """
        Args:
            model_name: 모델 이름 또는 키 (default, small, medium, large)
            device: 장치 (cuda, cpu, auto)
            max_length: 최대 토큰 길이 (기본값: 8192)
            use_fp16: FP16 사용 여부
            use_flash_attention: Flash Attention 2 사용 여부
        """
        self.model_id = self.MODELS.get(model_name, model_name)
        self.max_length = max_length
        self.use_fp16 = use_fp16
        self.use_flash_attention = use_flash_attention
        
        # 장치 설정
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
        
        self.model = None
        self.tokenizer = None
        self.token_true_id = None
        self.token_false_id = None
        self.prefix_tokens = None
        self.suffix_tokens = None
        
        self._is_loaded = False
    
    def load(self):
        """모델 로드 (지연 로딩)"""
        if self._is_loaded:
            return
        
        try:
            from transformers import AutoTokenizer, AutoModelForCausalLM
            
            logger.info(f"Loading Qwen3 Reranker: {self.model_id}")
            
            # 토크나이저 로드
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_id,
                padding_side='left'
            )
            
            # 모델 로드
            model_kwargs = {}
            
            if self.use_fp16 and self.device == "cuda":
                model_kwargs["torch_dtype"] = torch.float16
            
            if self.use_flash_attention:
                model_kwargs["attn_implementation"] = "flash_attention_2"
            
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_id,
                **model_kwargs
            )
            
            if self.device == "cuda":
                self.model = self.model.cuda()
            
            self.model.eval()
            
            # 토큰 ID 설정
            self.token_true_id = self.tokenizer.convert_tokens_to_ids("yes")
            self.token_false_id = self.tokenizer.convert_tokens_to_ids("no")
            
            # Prefix/Suffix 설정
            prefix = "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n"
            suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
            
            self.prefix_tokens = self.tokenizer.encode(prefix, add_special_tokens=False)
            self.suffix_tokens = self.tokenizer.encode(suffix, add_special_tokens=False)
            
            self._is_loaded = True
            logger.info(f"Qwen3 Reranker loaded successfully on {self.device}")
            
        except Exception as e:
            logger.error(f"Failed to load Qwen3 Reranker: {e}")
            raise
    
    def _format_instruction(
        self,
        instruction: str,
        query: str,
        doc: str
    ) -> str:
        """Instruction 포맷 생성"""
        return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}"
    
    def _process_inputs(self, pairs: List[str]) -> Dict[str, torch.Tensor]:
        """입력 처리"""
        inputs = self.tokenizer(
            pairs,
            padding=False,
            truncation='longest_first',
            return_attention_mask=False,
            max_length=self.max_length - len(self.prefix_tokens) - len(self.suffix_tokens)
        )
        
        # Prefix/Suffix 추가
        for i, ele in enumerate(inputs['input_ids']):
            inputs['input_ids'][i] = self.prefix_tokens + ele + self.suffix_tokens
        
        # 패딩 및 텐서 변환
        inputs = self.tokenizer.pad(
            inputs,
            padding=True,
            return_tensors="pt",
            max_length=self.max_length
        )
        
        # 장치로 이동
        for key in inputs:
            inputs[key] = inputs[key].to(self.model.device)
        
        return inputs
    
    @torch.no_grad()
    def _compute_logits(self, inputs: Dict[str, torch.Tensor]) -> List[float]:
        """관련성 점수 계산"""
        batch_scores = self.model(**inputs).logits[:, -1, :]
        
        true_vector = batch_scores[:, self.token_true_id]
        false_vector = batch_scores[:, self.token_false_id]
        
        batch_scores = torch.stack([false_vector, true_vector], dim=1)
        batch_scores = torch.nn.functional.log_softmax(batch_scores, dim=1)
        
        scores = batch_scores[:, 1].exp().tolist()
        return scores
    
    def rerank(
        self,
        query: str,
        documents: List[str],
        instruction: Optional[str] = None,
        task_type: str = "retrieval",
        top_k: Optional[int] = None,
        return_scores: bool = True,
        batch_size: int = 8,
    ) -> List[RerankResult]:
        """
        문서 리랭킹 수행
        
        Args:
            query: 검색 쿼리
            documents: 문서 리스트
            instruction: 커스텀 Instruction (None이면 task_type 기반 기본값 사용)
            task_type: 작업 유형 (retrieval, qa, finance, code, semantic)
            top_k: 반환할 상위 결과 수 (None이면 전체 반환)
            return_scores: 점수 포함 여부
            batch_size: 배치 크기
            
        Returns:
            RerankResult 리스트 (점수 내림차순 정렬)
        """
        # 모델 로드
        self.load()
        
        if not documents:
            return []
        
        # Instruction 설정
        if instruction is None:
            instruction = self.DEFAULT_INSTRUCTIONS.get(
                task_type,
                self.DEFAULT_INSTRUCTIONS["retrieval"]
            )
        
        # 입력 포맷팅
        pairs = [
            self._format_instruction(instruction, query, doc)
            for doc in documents
        ]
        
        # 배치 처리
        all_scores = []
        
        for i in range(0, len(pairs), batch_size):
            batch_pairs = pairs[i:i + batch_size]
            inputs = self._process_inputs(batch_pairs)
            scores = self._compute_logits(inputs)
            all_scores.extend(scores)
        
        # 결과 생성
        results = [
            RerankResult(
                content=doc,
                score=score,
                original_index=idx,
            )
            for idx, (doc, score) in enumerate(zip(documents, all_scores))
        ]
        
        # 점수 기준 정렬
        results.sort(key=lambda x: x.score, reverse=True)
        
        # top_k 적용
        if top_k is not None:
            results = results[:top_k]
        
        return results
    
    def rerank_with_metadata(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        content_key: str = "content",
        instruction: Optional[str] = None,
        task_type: str = "retrieval",
        top_k: Optional[int] = None,
        batch_size: int = 8,
    ) -> List[RerankResult]:
        """
        메타데이터를 포함한 문서 리랭킹
        
        Args:
            query: 검색 쿼리
            documents: 문서 딕셔너리 리스트 (content_key 필드 필요)
            content_key: 문서 내용 키
            instruction: 커스텀 Instruction
            task_type: 작업 유형
            top_k: 반환할 상위 결과 수
            batch_size: 배치 크기
            
        Returns:
            RerankResult 리스트 (메타데이터 포함)
        """
        # 모델 로드
        self.load()
        
        if not documents:
            return []
        
        # 문서 내용 추출
        contents = [doc.get(content_key, "") for doc in documents]
        
        # 리랭킹 수행
        results = self.rerank(
            query=query,
            documents=contents,
            instruction=instruction,
            task_type=task_type,
            top_k=None,  # 일단 전체 결과
            batch_size=batch_size,
        )
        
        # 메타데이터 추가
        for result in results:
            original_doc = documents[result.original_index]
            result.metadata = {
                k: v for k, v in original_doc.items()
                if k != content_key
            }
        
        # top_k 적용
        if top_k is not None:
            results = results[:top_k]
        
        return results
    
    def compute_score(
        self,
        query: str,
        document: str,
        instruction: Optional[str] = None,
        task_type: str = "retrieval",
    ) -> float:
        """
        단일 Query-Document 쌍의 관련성 점수 계산
        
        Args:
            query: 검색 쿼리
            document: 문서
            instruction: 커스텀 Instruction
            task_type: 작업 유형
            
        Returns:
            관련성 점수 (0~1)
        """
        results = self.rerank(
            query=query,
            documents=[document],
            instruction=instruction,
            task_type=task_type,
        )
        
        return results[0].score if results else 0.0


class RerankerManager:
    """리랭커 관리 클래스 (싱글톤 패턴)"""
    
    _instance = None
    _reranker = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def get_reranker(
        cls,
        model_name: str = "default",
        **kwargs
    ) -> Qwen3Reranker:
        """
        리랭커 인스턴스 반환
        
        Args:
            model_name: 모델 이름
            **kwargs: Qwen3Reranker 초기화 인자
            
        Returns:
            Qwen3Reranker 인스턴스
        """
        if cls._reranker is None:
            cls._reranker = Qwen3Reranker(model_name=model_name, **kwargs)
        return cls._reranker
    
    @classmethod
    def reset(cls):
        """리랭커 인스턴스 초기화"""
        if cls._reranker is not None:
            del cls._reranker
            cls._reranker = None
            
            # GPU 메모리 정리
            if torch.cuda.is_available():
                torch.cuda.empty_cache()


def rerank_documents(
    query: str,
    documents: List[str],
    top_k: int = 10,
    task_type: str = "finance",
    model_name: str = "default",
) -> List[RerankResult]:
    """
    문서 리랭킹 편의 함수
    
    Args:
        query: 검색 쿼리
        documents: 문서 리스트
        top_k: 반환할 상위 결과 수
        task_type: 작업 유형 (finance 기본값)
        model_name: 모델 이름
        
    Returns:
        RerankResult 리스트
        
    Example:
        >>> docs = ["삼성전자 주가 분석...", "애플 실적 발표...", ...]
        >>> results = rerank_documents("삼성전자 실적", docs, top_k=5)
        >>> for r in results:
        ...     print(f"[{r.score:.3f}] {r.content[:50]}...")
    """
    reranker = RerankerManager.get_reranker(model_name=model_name)
    return reranker.rerank(
        query=query,
        documents=documents,
        task_type=task_type,
        top_k=top_k,
    )
