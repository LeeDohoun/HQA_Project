#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 파일: pipeline_runner.py
"""
HQA 데이터 수집/인덱싱 파이프라인 CLI 실행기

사용법:
    # 특정 종목 데이터 수집 + 인덱싱
    python pipeline_runner.py ingest --code 005930 --name 삼성전자
    
    # 여러 종목 일괄 수집
    python pipeline_runner.py ingest-batch --codes 005930,000660,035420
    
    # PDF 파일/디렉토리 직접 인덱싱
    python pipeline_runner.py index-pdf --path ./data/reports/example.pdf
    python pipeline_runner.py index-dir --path ./data/reports --pattern "*.pdf"
    
    # 검색 테스트
    python pipeline_runner.py search --query "삼성전자 실적"
    
    # 상태 확인
    python pipeline_runner.py status
    
    # 임베딩 재구축
    python pipeline_runner.py rebuild --code 005930
"""

import argparse
import sys
import os
from typing import List, Optional
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def get_pipeline():
    """파이프라인 인스턴스 반환"""
    from src.data_pipeline import DataIngestionPipeline
    return DataIngestionPipeline(
        db_path="./database/raw_data.db",
        files_dir="./data/files",
        vector_persist_dir="./database/chroma_db",
        collection_name="stock_data",
        use_reranker=True,
        retrieval_k=20,
        rerank_top_k=3
    )


# ==================== 종목 코드 매핑 ====================

STOCK_CODES = {
    # 대형주
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "035720": "카카오",
    "051910": "LG화학",
    "006400": "삼성SDI",
    "207940": "삼성바이오로직스",
    "005380": "현대차",
    "000270": "기아",
    "068270": "셀트리온",
    "028260": "삼성물산",
    "105560": "KB금융",
    "055550": "신한지주",
    "086790": "하나금융지주",
    "066570": "LG전자",
    "096770": "SK이노베이션",
    "034730": "SK",
    "015760": "한국전력",
    "003670": "포스코홀딩스",
    "033780": "KT&G",
}


def get_stock_name(code: str) -> str:
    """종목코드로 종목명 조회"""
    return STOCK_CODES.get(code, f"종목{code}")


# ==================== CLI 커맨드 ====================

def cmd_ingest(args):
    """특정 종목 데이터 수집 + 인덱싱"""
    print(f"\n{'='*60}")
    print(f"🚀 데이터 수집/인덱싱 시작")
    print(f"   종목: {args.name} ({args.code})")
    print(f"{'='*60}")
    
    pipeline = get_pipeline()
    
    results = pipeline.ingest_stock_data(
        stock_code=args.code,
        stock_name=args.name,
        include_reports=not args.no_reports,
        include_news=not args.no_news,
        include_dart=not args.no_dart,
        include_price=not args.no_price,
        auto_embed=not args.no_embed
    )
    
    return results


def cmd_ingest_batch(args):
    """여러 종목 일괄 수집"""
    codes = args.codes.split(",")
    
    print(f"\n{'='*60}")
    print(f"🚀 일괄 수집 시작 ({len(codes)}개 종목)")
    print(f"{'='*60}")
    
    pipeline = get_pipeline()
    all_results = []
    
    for code in codes:
        code = code.strip()
        name = get_stock_name(code)
        
        print(f"\n📊 [{name}({code})] 처리 중...")
        
        try:
            results = pipeline.ingest_stock_data(
                stock_code=code,
                stock_name=name,
                include_reports=True,
                include_news=True,
                include_dart=True,
                include_price=True,
                auto_embed=True
            )
            all_results.append(results)
        except Exception as e:
            print(f"   ❌ 오류: {e}")
    
    # 요약
    print(f"\n{'='*60}")
    print(f"📊 일괄 수집 완료")
    print(f"{'='*60}")
    
    for result in all_results:
        print(f"   - {result['stock_name']}: "
              f"수집 {result['collected']['reports']+result['collected']['news']+result['collected']['disclosures']}건, "
              f"임베딩 {result['embedded']['reports']+result['embedded']['news']+result['embedded']['disclosures']}건")
    
    return all_results


def cmd_index_pdf(args):
    """PDF 파일 직접 인덱싱"""
    print(f"\n📄 PDF 인덱싱: {args.path}")
    
    if not os.path.exists(args.path):
        print(f"❌ 파일이 존재하지 않습니다: {args.path}")
        return None
    
    pipeline = get_pipeline()
    
    metadata = {}
    if args.stock_code:
        metadata["stock_code"] = args.stock_code
    if args.stock_name:
        metadata["stock_name"] = args.stock_name
    if args.data_type:
        metadata["data_type"] = args.data_type
    
    result = pipeline.index_pdf(
        file_path=args.path,
        metadata=metadata if metadata else None
    )
    
    return result


def cmd_index_dir(args):
    """디렉토리 일괄 인덱싱"""
    print(f"\n📁 디렉토리 인덱싱: {args.path}")
    
    if not os.path.isdir(args.path):
        print(f"❌ 디렉토리가 존재하지 않습니다: {args.path}")
        return None
    
    pipeline = get_pipeline()
    
    patterns = args.pattern.split(",") if args.pattern else ["*.pdf"]
    
    metadata = {}
    if args.stock_code:
        metadata["stock_code"] = args.stock_code
    if args.stock_name:
        metadata["stock_name"] = args.stock_name
    
    result = pipeline.index_directory(
        directory=args.path,
        file_patterns=patterns,
        metadata=metadata if metadata else None,
        recursive=not args.no_recursive
    )
    
    return result


def cmd_search(args):
    """검색 테스트"""
    print(f"\n🔍 검색: '{args.query}'")
    
    pipeline = get_pipeline()
    
    docs = pipeline.search(
        query=args.query,
        k=args.k,
        use_reranker=not args.no_reranker
    )
    
    print(f"\n📋 검색 결과: {len(docs)}건")
    print("="*60)
    
    for i, doc in enumerate(docs, 1):
        print(f"\n[{i}] {doc.metadata.get('data_type', 'unknown').upper()}")
        print(f"    종목: {doc.metadata.get('stock_name', 'N/A')}")
        print(f"    출처: {doc.metadata.get('source', doc.metadata.get('source_url', 'N/A'))}")
        print(f"    내용: {doc.page_content[:200]}...")
    
    return docs


def cmd_status(args):
    """상태 확인"""
    print("\n📊 파이프라인 상태")
    print("="*60)
    
    pipeline = get_pipeline()
    stats = pipeline.get_stats()
    
    # 원본 데이터 상태
    raw = stats.get("raw_data", {})
    print(f"\n💾 원본 데이터 (SQLite)")
    print(f"   - 리포트: {raw.get('reports', 0)}건")
    print(f"   - 뉴스: {raw.get('news', 0)}건")
    print(f"   - 공시: {raw.get('disclosures', 0)}건")
    print(f"   - 주가: {raw.get('price', 0)}건")
    print(f"   - DB 크기: {raw.get('db_size_mb', 'N/A')}MB")
    
    # 벡터 저장소 상태
    vector = stats.get("vector_store", {})
    print(f"\n🧠 벡터 저장소 (ChromaDB)")
    print(f"   - 문서 수: {vector.get('total_documents', 0)}건")
    print(f"   - 저장소 크기: {vector.get('size_mb', 'N/A')}MB")
    
    return stats


def cmd_rebuild(args):
    """임베딩 재구축"""
    print(f"\n🔄 임베딩 재구축")
    
    if args.code:
        print(f"   대상: {args.code}")
    else:
        print(f"   대상: 전체")
    
    confirm = input("\n⚠️ 정말 재구축하시겠습니까? (y/N): ")
    if confirm.lower() != 'y':
        print("취소되었습니다.")
        return None
    
    pipeline = get_pipeline()
    result = pipeline.rebuild_embeddings(stock_code=args.code)
    
    return result


def cmd_clear(args):
    """벡터 저장소 초기화"""
    print("\n⚠️ 벡터 저장소 초기화")
    print("   이 작업은 모든 임베딩을 삭제합니다!")
    
    confirm = input("\n정말 초기화하시겠습니까? (yes를 입력): ")
    if confirm != 'yes':
        print("취소되었습니다.")
        return None
    
    pipeline = get_pipeline()
    pipeline.clear_vector_store()
    
    print("✅ 벡터 저장소가 초기화되었습니다.")
    return True


# ==================== 메인 ====================

def main():
    parser = argparse.ArgumentParser(
        description="HQA 데이터 수집/인덱싱 파이프라인 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 삼성전자 데이터 수집
  python pipeline_runner.py ingest --code 005930 --name 삼성전자

  # 여러 종목 일괄 수집
  python pipeline_runner.py ingest-batch --codes 005930,000660,035420

  # PDF 파일 인덱싱
  python pipeline_runner.py index-pdf --path ./reports/sample.pdf

  # 검색
  python pipeline_runner.py search --query "삼성전자 반도체 실적"

  # 상태 확인
  python pipeline_runner.py status
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="명령어")
    
    # ingest 명령어
    ingest_parser = subparsers.add_parser("ingest", help="특정 종목 데이터 수집 + 인덱싱")
    ingest_parser.add_argument("--code", required=True, help="종목코드 (예: 005930)")
    ingest_parser.add_argument("--name", required=True, help="종목명 (예: 삼성전자)")
    ingest_parser.add_argument("--no-reports", action="store_true", help="리포트 수집 제외")
    ingest_parser.add_argument("--no-news", action="store_true", help="뉴스 수집 제외")
    ingest_parser.add_argument("--no-dart", action="store_true", help="DART 공시 제외")
    ingest_parser.add_argument("--no-price", action="store_true", help="주가 데이터 제외")
    ingest_parser.add_argument("--no-embed", action="store_true", help="자동 임베딩 제외")
    
    # ingest-batch 명령어
    batch_parser = subparsers.add_parser("ingest-batch", help="여러 종목 일괄 수집")
    batch_parser.add_argument("--codes", required=True, help="종목코드 목록 (쉼표 구분, 예: 005930,000660)")
    
    # index-pdf 명령어
    pdf_parser = subparsers.add_parser("index-pdf", help="PDF 파일 직접 인덱싱")
    pdf_parser.add_argument("--path", required=True, help="PDF 파일 경로")
    pdf_parser.add_argument("--stock-code", help="종목코드 (메타데이터)")
    pdf_parser.add_argument("--stock-name", help="종목명 (메타데이터)")
    pdf_parser.add_argument("--data-type", default="report", help="데이터 유형 (report, news, etc)")
    
    # index-dir 명령어
    dir_parser = subparsers.add_parser("index-dir", help="디렉토리 일괄 인덱싱")
    dir_parser.add_argument("--path", required=True, help="디렉토리 경로")
    dir_parser.add_argument("--pattern", default="*.pdf", help="파일 패턴 (쉼표 구분, 예: *.pdf,*.txt)")
    dir_parser.add_argument("--stock-code", help="종목코드 (메타데이터)")
    dir_parser.add_argument("--stock-name", help="종목명 (메타데이터)")
    dir_parser.add_argument("--no-recursive", action="store_true", help="하위 디렉토리 제외")
    
    # search 명령어
    search_parser = subparsers.add_parser("search", help="검색 테스트")
    search_parser.add_argument("--query", required=True, help="검색 쿼리")
    search_parser.add_argument("--k", type=int, default=5, help="결과 개수 (기본: 5)")
    search_parser.add_argument("--no-reranker", action="store_true", help="리랭커 비활성화")
    
    # status 명령어
    subparsers.add_parser("status", help="상태 확인")
    
    # rebuild 명령어
    rebuild_parser = subparsers.add_parser("rebuild", help="임베딩 재구축")
    rebuild_parser.add_argument("--code", help="특정 종목만 재구축")
    
    # clear 명령어
    subparsers.add_parser("clear", help="벡터 저장소 초기화 (주의!)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # 명령어 실행
    commands = {
        "ingest": cmd_ingest,
        "ingest-batch": cmd_ingest_batch,
        "index-pdf": cmd_index_pdf,
        "index-dir": cmd_index_dir,
        "search": cmd_search,
        "status": cmd_status,
        "rebuild": cmd_rebuild,
        "clear": cmd_clear,
    }
    
    start_time = datetime.now()
    
    try:
        result = commands[args.command](args)
        
        elapsed = datetime.now() - start_time
        print(f"\n⏱️ 실행 시간: {elapsed.total_seconds():.1f}초")
        
    except KeyboardInterrupt:
        print("\n\n⚠️ 사용자에 의해 중단되었습니다.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
