// 파일: frontend/src/app/page.tsx
/**
 * HQA 메인 페이지
 * 
 * 종목 검색 → 분석 요청 → 실시간 진행 → 결과 표시
 */
'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  searchStocks,
  requestAnalysis,
  getAnalysisResult,
  streamAnalysisProgress,
  type StockInfo,
  type AnalysisResult,
  type AgentProgress,
} from '@/lib/api';

export default function HomePage() {
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<StockInfo[]>([]);
  const [selectedStock, setSelectedStock] = useState<StockInfo | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [progress, setProgress] = useState<AgentProgress[]>([]);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 종목 검색
  const handleSearch = useCallback(async (q: string) => {
    if (q.length < 1) {
      setSearchResults([]);
      return;
    }
    try {
      const data = await searchStocks(q);
      setSearchResults(data.results);
    } catch (e) {
      console.error('Search error:', e);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => handleSearch(query), 300);
    return () => clearTimeout(timer);
  }, [query, handleSearch]);

  // 분석 시작
  const startAnalysis = async (mode: 'full' | 'quick') => {
    if (!selectedStock) return;
    setLoading(true);
    setError(null);
    setProgress([]);
    setResult(null);

    try {
      const task = await requestAnalysis(selectedStock.name, selectedStock.code, mode);
      setTaskId(task.task_id);

      // SSE 스트리밍으로 진행 상황 수신
      streamAnalysisProgress(
        task.task_id,
        (event) => setProgress((prev) => [...prev, event]),
        async () => {
          // 완료 시 결과 조회
          const analysisResult = await getAnalysisResult(task.task_id);
          setResult(analysisResult);
          setLoading(false);
        },
        (err) => {
          setError(err);
          setLoading(false);
        },
      );
    } catch (e: any) {
      setError(e.message);
      setLoading(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      {/* Header */}
      <header className="text-center mb-12">
        <h1 className="text-4xl font-bold text-blue-600 mb-2">📈 HQA</h1>
        <p className="text-gray-500">Hegemony Quantitative Analyst — AI 멀티 에이전트 주식 분석</p>
      </header>

      {/* 종목 검색 */}
      <div className="max-w-xl mx-auto mb-8">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="종목명 또는 종목코드 검색 (예: 삼성전자, 005930)"
          className="w-full px-4 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent text-lg"
        />

        {/* 검색 결과 드롭다운 */}
        {searchResults.length > 0 && (
          <div className="mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-60 overflow-y-auto">
            {searchResults.map((stock) => (
              <button
                key={stock.code}
                onClick={() => {
                  setSelectedStock(stock);
                  setQuery(`${stock.name} (${stock.code})`);
                  setSearchResults([]);
                }}
                className="w-full text-left px-4 py-3 hover:bg-blue-50 border-b border-gray-100 last:border-0"
              >
                <span className="font-medium">{stock.name}</span>
                <span className="text-gray-400 ml-2">{stock.code}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 선택된 종목 & 분석 버튼 */}
      {selectedStock && (
        <div className="max-w-xl mx-auto mb-8 bg-white p-6 rounded-lg shadow">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-bold">{selectedStock.name}</h2>
              <span className="text-gray-400">{selectedStock.code}</span>
            </div>
            <button
              onClick={() => {
                setSelectedStock(null);
                setQuery('');
                setResult(null);
              }}
              className="text-gray-400 hover:text-gray-600"
            >
              ✕
            </button>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => startAnalysis('full')}
              disabled={loading}
              className="flex-1 bg-blue-600 text-white py-3 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? '분석 중...' : '🚀 전체 분석'}
            </button>
            <button
              onClick={() => startAnalysis('quick')}
              disabled={loading}
              className="flex-1 bg-gray-100 text-gray-700 py-3 rounded-lg font-medium hover:bg-gray-200 disabled:opacity-50"
            >
              {loading ? '분석 중...' : '⚡ 빠른 분석'}
            </button>
          </div>
        </div>
      )}

      {/* 진행 상황 (SSE) */}
      {loading && progress.length > 0 && (
        <div className="max-w-xl mx-auto mb-8 bg-white p-6 rounded-lg shadow">
          <h3 className="font-bold mb-4">📊 분석 진행 중...</h3>
          <div className="space-y-3">
            {progress.map((p, i) => (
              <div key={i} className="flex items-center gap-3">
                <span className={`w-2 h-2 rounded-full ${
                  p.status === 'completed' ? 'bg-green-500' :
                  p.status === 'error' ? 'bg-red-500' : 'bg-yellow-500 animate-pulse'
                }`} />
                <span className="font-medium capitalize">{p.agent}</span>
                <span className="text-gray-500 text-sm">{p.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 에러 */}
      {error && (
        <div className="max-w-xl mx-auto mb-8 bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
          ❌ {error}
        </div>
      )}

      {/* 분석 결과 */}
      {result && result.status === 'completed' && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-2xl font-bold mb-6">
            📊 {result.stock.name} 분석 결과
          </h2>

          {/* 최종 판단 */}
          {result.final_decision && (
            <div className="mb-8 p-6 bg-gradient-to-r from-blue-50 to-purple-50 rounded-lg">
              <div className="flex items-center gap-4 mb-4">
                <span className="text-3xl">
                  {result.final_decision.action?.includes('매수') ? '🟢' :
                   result.final_decision.action?.includes('매도') ? '🔴' : '🟡'}
                </span>
                <div>
                  <h3 className="text-2xl font-bold">{result.final_decision.action}</h3>
                  <p className="text-gray-500">
                    확신도 {result.final_decision.confidence}% · 
                    리스크 {result.final_decision.risk_level} · 
                    종합 {result.final_decision.total_score}점
                  </p>
                </div>
              </div>
              <p className="text-gray-700">{result.final_decision.summary}</p>
            </div>
          )}

          {/* 에이전트별 점수 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {result.scores.map((score) => (
              <div key={score.agent} className="border rounded-lg p-4">
                <h4 className="font-bold capitalize mb-2">
                  {score.agent === 'analyst' ? '🔍 Analyst' :
                   score.agent === 'quant' ? '📈 Quant' : '📉 Chartist'}
                </h4>
                <div className="text-3xl font-bold mb-1">
                  {score.total_score}<span className="text-lg text-gray-400">/{score.max_score}</span>
                </div>
                {score.grade && <span className="text-sm text-gray-500">등급: {score.grade}</span>}
                {score.opinion && (
                  <p className="text-sm text-gray-600 mt-2">{score.opinion}</p>
                )}
              </div>
            ))}
          </div>

          {/* 메타 */}
          <p className="text-gray-400 text-sm mt-6 text-right">
            소요시간: {result.duration_seconds?.toFixed(1)}초 · 
            품질: {result.research_quality || '-'}등급
          </p>
        </div>
      )}

      {/* 푸터 */}
      <footer className="mt-16 text-center text-gray-400 text-sm">
        HQA v1.0.0 · Powered by Gemini AI · ⚠️ 투자 권유가 아닙니다.
      </footer>
    </div>
  );
}
