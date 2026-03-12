// 파일: frontend/src/app/charts/page.tsx
'use client';

/**
 * 실시간 차트 페이지
 *
 * 종목 검색 → lightweight-charts 캔들스틱 차트 + KIS API 데이터
 * - 과거 캔들: REST API (/api/v1/charts/{code}/history)
 * - 실시간 업데이트: WebSocket
 * - 좌측 스크롤 시 무한 스크롤 (페이지네이션)
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import CandlestickChart, {
  type CandlestickChartRef,
} from '@/components/CandlestickChart';
import { useChartWebSocket } from '@/lib/useChartWebSocket';
import {
  getHistoricalCandles,
  searchStocks,
  ApiRequestError,
  type CandleData,
  type StockInfo,
} from '@/lib/api';

// ── 상수 ──

const POPULAR_STOCKS: StockInfo[] = [
  { name: '삼성전자', code: '005930' },
  { name: 'SK하이닉스', code: '000660' },
  { name: 'LG에너지솔루션', code: '373220' },
  { name: '삼성바이오로직스', code: '207940' },
  { name: '현대자동차', code: '005380' },
  { name: 'NAVER', code: '035420' },
  { name: '카카오', code: '035720' },
  { name: 'POSCO홀딩스', code: '005490' },
];

const TIMEFRAMES = [
  { label: '1분', value: '1m' },
  { label: '3분', value: '3m' },
  { label: '5분', value: '5m' },
  { label: '10분', value: '10m' },
  { label: '15분', value: '15m' },
  { label: '30분', value: '30m' },
  { label: '45분', value: '45m' },
  { label: '1시간', value: '1h' },
];

// 타임프레임별 초기 로딩 캔들 수
// 키움 API는 1회 최대 900개 반환 → 넉넉하게 요청 가능
const INITIAL_COUNTS: Record<string, number> = {
  '1m': 200,
  '3m': 150,
  '5m': 120,
  '10m': 80,
  '15m': 60,
  '30m': 40,
  '45m': 30,
  '1h': 24,
};

// ── 컴포넌트 ──

export default function ChartsPage() {
  // 종목 검색
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<StockInfo[]>([]);
  const [selectedStock, setSelectedStock] = useState<StockInfo | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);

  // 차트 상태
  const [timeframe, setTimeframe] = useState('1m');
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [loading, setLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  // 페이지네이션 refs
  const hasMoreRef = useRef(true);
  const loadingMoreRef = useRef(false);
  const selectedStockRef = useRef<StockInfo | null>(null);
  const timeframeRef = useRef(timeframe);
  const candlesRef = useRef<CandleData[]>([]);

  // 차트 ref
  const chartRef = useRef<CandlestickChartRef>(null);

  // refs 동기화
  selectedStockRef.current = selectedStock;
  timeframeRef.current = timeframe;
  candlesRef.current = candles;

  // ── 종목 검색 (디바운스) ──

  useEffect(() => {
    if (query.length < 1) {
      setSearchResults([]);
      return;
    }

    const timer = setTimeout(async () => {
      setSearchLoading(true);
      try {
        const result = await searchStocks(query);
        setSearchResults(result.results);
      } catch {
        // 백엔드 실패 시 로컬 인기 종목 + 직접 코드 입력 지원
        const q = query.toLowerCase();
        const localResults = POPULAR_STOCKS.filter(
          (s) => s.name.toLowerCase().includes(q) || s.code.includes(q),
        );
        if (/^\d{6}$/.test(query) && !localResults.some((s) => s.code === query)) {
          localResults.unshift({ name: query, code: query });
        }
        setSearchResults(localResults);
      } finally {
        setSearchLoading(false);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [query]);

  // ── 초기 캔들 로딩 ──

  useEffect(() => {
    if (!selectedStock) {
      setCandles([]);
      return;
    }

    let cancelled = false;
    const loadInitial = async () => {
      setLoading(true);
      setChartError(null);
      hasMoreRef.current = true;
      try {
        const count = INITIAL_COUNTS[timeframe] || 120;
        const { candles: data, hasMore } = await getHistoricalCandles(selectedStock.code, timeframe, count);
        if (!cancelled) {
          setCandles(data);
          hasMoreRef.current = hasMore;
        }
      } catch (err) {
        console.error('캔들 로딩 실패:', err);
        if (!cancelled) {
          setCandles([]);
          if (err instanceof ApiRequestError) {
            // 표준 에러 코드 기반 메시지
            if (err.errorCode === 'CHART_API_NOT_CONFIGURED') {
              setChartError('차트 API가 설정되지 않았습니다.');
            } else if (err.errorCode === 'CHART_NO_DATA') {
              setChartError('해당 종목의 차트 데이터가 없습니다.');
            } else {
              setChartError('차트 로딩에 실패하였습니다.');
            }
          } else {
            setChartError('차트 로딩에 실패하였습니다.');
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadInitial();
    return () => { cancelled = true; };
  }, [selectedStock, timeframe]);

  // ── 무한 스크롤: 과거 데이터 로드 ──

  const loadMoreData = useCallback(async () => {
    if (!hasMoreRef.current || loadingMoreRef.current) return;
    const stock = selectedStockRef.current;
    const tf = timeframeRef.current;
    const currentCandles = candlesRef.current;
    if (!stock || currentCandles.length === 0) return;

    loadingMoreRef.current = true;
    try {
      const oldest = currentCandles[0];
      const count = INITIAL_COUNTS[tf] || 120;
      const { candles: olderCandles, hasMore } = await getHistoricalCandles(
        stock.code,
        tf,
        count,
        oldest.time,
      );

      hasMoreRef.current = hasMore;

      if (olderCandles.length > 0) {
        // 차트에 prepend
        chartRef.current?.prependData(olderCandles);
        // candles state도 업데이트 (ref 동기화용)
        setCandles((prev) => {
          const existingTimes = new Set(prev.map((c) => c.time));
          const unique = olderCandles.filter((c) => !existingTimes.has(c.time));
          return [...unique, ...prev].sort((a, b) => a.time - b.time);
        });
      }
    } catch (err) {
      console.error('추가 캔들 로딩 실패:', err);
    } finally {
      loadingMoreRef.current = false;
    }
  }, []);

  // ── WebSocket 실시간 업데이트 ──

  const { connected, lastTick } = useChartWebSocket(
    selectedStock?.code ?? null,
    timeframe,
    {
      onCandleUpdate: (candle) => {
        chartRef.current?.updateCandle(candle);
      },
      onCandleComplete: (candle) => {
        chartRef.current?.addCompletedCandle(candle);
      },
    },
  );

  // ── 핸들러 ──

  const handleSelectStock = useCallback((stock: StockInfo) => {
    setSelectedStock(stock);
    setQuery('');
    setSearchResults([]);
  }, []);

  const handleClearStock = useCallback(() => {
    setSelectedStock(null);
    setQuery('');
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        if (searchResults.length > 0) {
          handleSelectStock(searchResults[0]);
        } else if (/^\d{6}$/.test(query)) {
          handleSelectStock({ name: query, code: query });
        }
      }
    },
    [query, searchResults, handleSelectStock],
  );

  // ── 렌더 ──

  return (
    <div className="min-h-screen bg-[#0f0f23] text-gray-200">
      {/* 헤더 */}
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-bold text-white">HQA 실시간 차트</h1>
          <a
            href="/"
            className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
          >
            ← 분석 페이지
          </a>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">
        {/* 검색 & 종목 선택 */}
        <div className="flex items-center gap-4">
          <div className="relative flex-1 max-w-md">
            <input
              type="text"
              value={
                selectedStock
                  ? `${selectedStock.name} (${selectedStock.code})`
                  : query
              }
              onChange={(e) => {
                if (selectedStock) handleClearStock();
                setQuery(e.target.value);
              }}
              onKeyDown={handleKeyDown}
              placeholder="종목명 또는 종목코드 입력... (예: 삼성전자, 005930)"
              className="w-full px-4 py-2.5 bg-gray-900 border border-gray-700 rounded-lg
                         text-white placeholder-gray-500 focus:outline-none focus:border-indigo-500
                         transition-colors"
            />

            {/* 검색 결과 드롭다운 */}
            {searchResults.length > 0 && !selectedStock && (
              <div
                className="absolute z-50 mt-1 w-full bg-gray-900 border border-gray-700
                            rounded-lg shadow-xl max-h-60 overflow-y-auto"
              >
                {searchResults.map((stock) => (
                  <button
                    key={stock.code}
                    onClick={() => handleSelectStock(stock)}
                    className="w-full px-4 py-2.5 text-left hover:bg-gray-800 transition-colors
                               flex items-center justify-between"
                  >
                    <span className="text-white font-medium">{stock.name}</span>
                    <span className="text-gray-400 text-sm">{stock.code}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 종목 해제 */}
          {selectedStock && (
            <button
              onClick={handleClearStock}
              className="px-3 py-2 text-sm text-gray-400 hover:text-white
                         bg-gray-800 rounded-lg transition-colors"
            >
              초기화
            </button>
          )}
        </div>

        {/* 인기 종목 (종목 미선택 시) */}
        {!selectedStock && (
          <div className="flex flex-wrap gap-2">
            {POPULAR_STOCKS.map((stock) => (
              <button
                key={stock.code}
                onClick={() => handleSelectStock(stock)}
                className="px-3 py-1.5 text-sm bg-gray-900 border border-gray-700 rounded-lg
                           text-gray-300 hover:text-white hover:border-indigo-500 transition-colors"
              >
                {stock.name}
              </button>
            ))}
          </div>
        )}

        {/* 선택된 종목이 있을 때 */}
        {selectedStock && (
          <>
            {/* 타임프레임 버튼 + 실시간 가격 */}
            <div className="flex items-center justify-between">
              <div className="flex gap-1">
                {TIMEFRAMES.map((tf) => (
                  <button
                    key={tf.value}
                    onClick={() => setTimeframe(tf.value)}
                    className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                      timeframe === tf.value
                        ? 'bg-indigo-600 text-white'
                        : 'bg-gray-800 text-gray-400 hover:text-white'
                    }`}
                  >
                    {tf.label}
                  </button>
                ))}
              </div>

              {/* 실시간 가격 & 연결 상태 */}
              <div className="flex items-center gap-4">
                {lastTick && (
                  <div className="text-right">
                    <span className="text-lg font-bold text-white">
                      {lastTick.price.toLocaleString()}원
                    </span>
                    <span
                      className={`ml-2 text-sm ${
                        lastTick.change_rate >= 0
                          ? 'text-green-400'
                          : 'text-red-400'
                      }`}
                    >
                      {lastTick.change_rate >= 0 ? '+' : ''}
                      {lastTick.change_rate.toFixed(2)}%
                    </span>
                  </div>
                )}
                <div
                  className={`w-2 h-2 rounded-full ${
                    connected ? 'bg-green-400' : 'bg-red-400'
                  }`}
                  title={connected ? '실시간 연결됨' : '연결 끊김'}
                />
              </div>
            </div>

            {/* 차트 */}
            <div className="rounded-lg overflow-hidden border border-gray-800">
              {loading ? (
                <div className="flex items-center justify-center h-[500px] bg-[#1a1a2e]">
                  <div className="text-gray-400">차트 로딩 중...</div>
                </div>
              ) : chartError ? (
                <div className="flex flex-col items-center justify-center h-[500px] bg-[#1a1a2e]">
                  <svg
                    className="w-12 h-12 mb-3 text-red-400 opacity-60"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={1.5}
                      d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
                    />
                  </svg>
                  <p className="text-red-400 text-lg font-medium">{chartError}</p>
                  <button
                    onClick={() => {
                      setChartError(null);
                      setCandles([]);
                      // 재시도: timeframe 강제 리렌더
                      const tf = timeframe;
                      setTimeframe('');
                      setTimeout(() => setTimeframe(tf), 0);
                    }}
                    className="mt-4 px-4 py-2 text-sm bg-gray-800 text-gray-300
                               hover:text-white rounded-lg transition-colors"
                  >
                    다시 시도
                  </button>
                </div>
              ) : (
                <CandlestickChart
                  ref={chartRef}
                  historicalCandles={candles}
                  height={500}
                  stockName={selectedStock.name}
                  stockCode={selectedStock.code}
                  dark={true}
                  onLoadMore={loadMoreData}
                />
              )}
            </div>
          </>
        )}

        {/* 종목 미선택 시 안내 */}
        {!selectedStock && (
          <div className="flex flex-col items-center justify-center py-24 text-gray-500">
            <svg
              className="w-16 h-16 mb-4 opacity-30"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"
              />
            </svg>
            <p className="text-lg">
              종목을 검색하거나 위의 인기 종목을 클릭하세요
            </p>
            <p className="text-sm mt-1 text-gray-600">
              6자리 종목코드 직접 입력 후 Enter도 가능합니다
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
