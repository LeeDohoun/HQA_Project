// 파일: frontend/src/components/CandlestickChart.tsx
'use client';

/**
 * 실시간 캔들스틱 차트 컴포넌트
 *
 * TradingView lightweight-charts를 사용하여 캔들스틱 + 볼륨 차트를 렌더링합니다.
 * WebSocket을 통한 실시간 업데이트를 지원합니다.
 * 좌측 스크롤 시 자동으로 과거 데이터를 로딩합니다. (무한 스크롤)
 */

import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  forwardRef,
} from 'react';
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type Time,
  ColorType,
  CrosshairMode,
  type LogicalRange,
} from 'lightweight-charts';

import type { CandleData, TickData } from '@/lib/useChartWebSocket';

// ── 타입 ──

export interface CandlestickChartProps {
  /** 초기 과거 캔들 데이터 */
  historicalCandles?: CandleData[];
  /** 차트 높이 (px) */
  height?: number;
  /** 종목명 (워터마크용) */
  stockName?: string;
  /** 종목코드 */
  stockCode?: string;
  /** 다크 모드 */
  dark?: boolean;
  /** 좌측 끝 도달 시 더 많은 데이터 요청 콜백 */
  onLoadMore?: () => void;
}

export interface CandlestickChartRef {
  /** 캔들 업데이트 (실시간) */
  updateCandle: (candle: CandleData) => void;
  /** 완성된 캔들 추가 */
  addCompletedCandle: (candle: CandleData) => void;
  /** 틱으로 마지막 캔들 업데이트 */
  updateFromTick: (tick: TickData) => void;
  /** 차트 데이터 전체 교체 */
  setData: (candles: CandleData[]) => void;
  /** 기존 데이터 앞에 과거 캔들 추가 (스크롤 위치 유지) */
  prependData: (candles: CandleData[]) => void;
  /** 차트 시간 축 맞춤 */
  fitContent: () => void;
}

// ── 유틸 ──

// KST 오프셋 (lightweight-charts는 UTC로 표시하므로 +9시간 보정)
const KST_OFFSET = 9 * 3600;

function toCandlestick(c: CandleData): CandlestickData<Time> {
  return {
    time: (c.time + KST_OFFSET) as unknown as Time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  };
}

function toVolume(c: CandleData): HistogramData<Time> {
  const bullish = c.close >= c.open;
  return {
    time: (c.time + KST_OFFSET) as unknown as Time,
    value: c.volume || 0,
    color: bullish ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)',
  };
}

// ── 컴포넌트 ──

const CandlestickChart = forwardRef<CandlestickChartRef, CandlestickChartProps>(
  function CandlestickChart(
    {
      historicalCandles = [],
      height = 500,
      stockName = '',
      stockCode = '',
      dark = true,
      onLoadMore,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);
    const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
    const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

    // 현재 차트에 로드된 전체 캔들 데이터 (시간순 정렬, 중복 제거)
    const allCandlesRef = useRef<CandleData[]>([]);

    // onLoadMore ref (리렌더 없이 최신 참조)
    const onLoadMoreRef = useRef(onLoadMore);
    onLoadMoreRef.current = onLoadMore;

    // 무한 스크롤 쓰로틀링 & 프로그래밍 방식 스크롤 억제
    const loadMoreCooldownRef = useRef(false);
    const suppressScrollRef = useRef(false);

    // prependData 진행 중 플래그 — historicalCandles useEffect가 차트를 리셋하지 않도록
    const isPrependingRef = useRef(false);

    // 차트 초기화
    useEffect(() => {
      if (!containerRef.current) return;

      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height,
        layout: {
          background: {
            type: ColorType.Solid,
            color: dark ? '#1a1a2e' : '#ffffff',
          },
          textColor: dark ? '#d1d5db' : '#374151',
          fontFamily: "'Pretendard', -apple-system, sans-serif",
          fontSize: 12,
        },
        grid: {
          vertLines: { color: dark ? '#2d2d44' : '#e5e7eb' },
          horzLines: { color: dark ? '#2d2d44' : '#e5e7eb' },
        },
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: {
            color: dark ? '#6366f1' : '#4f46e5',
            width: 1,
            style: 2,
            labelBackgroundColor: dark ? '#6366f1' : '#4f46e5',
          },
          horzLine: {
            color: dark ? '#6366f1' : '#4f46e5',
            width: 1,
            style: 2,
            labelBackgroundColor: dark ? '#6366f1' : '#4f46e5',
          },
        },
        rightPriceScale: {
          borderColor: dark ? '#2d2d44' : '#e5e7eb',
          scaleMargins: {
            top: 0.1,
            bottom: 0.25,
          },
        },
        timeScale: {
          borderColor: dark ? '#2d2d44' : '#e5e7eb',
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 5,
          barSpacing: 8,
        },
        watermark: stockName
          ? {
              visible: true,
              text: `${stockName} (${stockCode})`,
              fontSize: 36,
              color: dark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)',
              horzAlign: 'center',
              vertAlign: 'center',
            }
          : { visible: false },
      });

      // 캔들스틱 시리즈
      const candleSeries = chart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderDownColor: '#ef5350',
        borderUpColor: '#26a69a',
        wickDownColor: '#ef5350',
        wickUpColor: '#26a69a',
      });

      // 볼륨 시리즈
      const volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: '',
      });

      volumeSeries.priceScale().applyOptions({
        scaleMargins: {
          top: 0.8,
          bottom: 0,
        },
      });

      chartRef.current = chart;
      candleSeriesRef.current = candleSeries;
      volumeSeriesRef.current = volumeSeries;

      // 과거 데이터 설정
      if (historicalCandles.length > 0) {
        const sorted = [...historicalCandles].sort((a, b) => a.time - b.time);
        allCandlesRef.current = sorted;
        suppressScrollRef.current = true;
        candleSeries.setData(sorted.map(toCandlestick));
        volumeSeries.setData(sorted.map(toVolume));
        chart.timeScale().fitContent();
        // fitContent 후 약간의 지연으로 suppress 해제
        setTimeout(() => { suppressScrollRef.current = false; }, 500);
      }

      // ── 무한 스크롤: 로드된 데이터의 절반 이상 스크롤 시 트리거 ──
      chart.timeScale().subscribeVisibleLogicalRangeChange((logicalRange: LogicalRange | null) => {
        if (!logicalRange || !onLoadMoreRef.current) return;

        // 프로그래밍 방식 스크롤(fitContent, prependData)은 무시
        if (suppressScrollRef.current) return;

        // 전체 로드된 캔들 수 기준으로 절반 지점 계산
        const totalBars = allCandlesRef.current.length;
        const halfPoint = Math.max(totalBars * 0.5, 10);

        // 사용자의 현재 뷰 왼쪽 끝이 전체 데이터의 절반 지점 이전이면 로드
        if (logicalRange.from <= halfPoint) {
          if (!loadMoreCooldownRef.current) {
            loadMoreCooldownRef.current = true;
            onLoadMoreRef.current();

            // 쿨다운: 2초 후 다시 요청 가능
            setTimeout(() => {
              loadMoreCooldownRef.current = false;
            }, 2000);
          }
        }
      });

      // 리사이즈 처리
      const resizeObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const { width } = entry.contentRect;
          chart.applyOptions({ width });
        }
      });
      resizeObserver.observe(containerRef.current);

      return () => {
        resizeObserver.disconnect();
        chart.remove();
        chartRef.current = null;
        candleSeriesRef.current = null;
        volumeSeriesRef.current = null;
      };
    }, [height, dark]);

    // 과거 데이터 변경 시 재설정 (초기 로딩 & 종목/타임프레임 변경)
    // prependData에 의한 변경은 무시 (prependData가 직접 차트를 업데이트함)
    useEffect(() => {
      if (!candleSeriesRef.current || !volumeSeriesRef.current) return;
      if (historicalCandles.length === 0) return;

      // prependData 중이면 스킵 — 이미 차트가 업데이트됨
      if (isPrependingRef.current) {
        return;
      }

      const sorted = [...historicalCandles].sort((a, b) => a.time - b.time);
      allCandlesRef.current = sorted;

      // fitContent 동안 loadMore 억제
      suppressScrollRef.current = true;
      candleSeriesRef.current.setData(sorted.map(toCandlestick));
      volumeSeriesRef.current.setData(sorted.map(toVolume));
      chartRef.current?.timeScale().scrollToRealTime();

      setTimeout(() => { suppressScrollRef.current = false; }, 500);
    }, [historicalCandles]);

    // 외부에서 사용할 메서드 노출
    const updateCandle = useCallback((candle: CandleData) => {
      if (!candleSeriesRef.current || !volumeSeriesRef.current) return;
      candleSeriesRef.current.update(toCandlestick(candle));
      volumeSeriesRef.current.update(toVolume(candle));
    }, []);

    const addCompletedCandle = useCallback((candle: CandleData) => {
      if (!candleSeriesRef.current || !volumeSeriesRef.current) return;
      candleSeriesRef.current.update(toCandlestick(candle));
      volumeSeriesRef.current.update(toVolume(candle));
    }, []);

    const updateFromTick = useCallback((_tick: TickData) => {
      // 틱으로는 최종 가격만 표시 (캔들 업데이트는 candle_update에서 처리)
    }, []);

    const setData = useCallback((candles: CandleData[]) => {
      if (!candleSeriesRef.current || !volumeSeriesRef.current) return;
      const sorted = [...candles].sort((a, b) => a.time - b.time);
      allCandlesRef.current = sorted;

      suppressScrollRef.current = true;
      candleSeriesRef.current.setData(sorted.map(toCandlestick));
      volumeSeriesRef.current.setData(sorted.map(toVolume));
      chartRef.current?.timeScale().scrollToRealTime();
      setTimeout(() => { suppressScrollRef.current = false; }, 500);
    }, []);

    const prependData = useCallback((newCandles: CandleData[]) => {
      if (!candleSeriesRef.current || !volumeSeriesRef.current || !chartRef.current) return;
      if (newCandles.length === 0) return;

      // 프로그래밍 방식 스크롤 억제
      suppressScrollRef.current = true;
      // historicalCandles useEffect가 차트를 리셋하지 않도록 플래그 설정
      // 카운터 방식: setCandles가 여러 번 렌더를 발생시킬 수 있으므로
      isPrependingRef.current = true;

      // 현재 보이는 범위 저장 (스크롤 위치 유지용)
      const timeScale = chartRef.current.timeScale();
      const visibleRange = timeScale.getVisibleLogicalRange();

      // 기존 데이터와 병합 + 중복 제거
      const existingTimes = new Set(allCandlesRef.current.map((c) => c.time));
      const uniqueNew = newCandles.filter((c) => !existingTimes.has(c.time));

      if (uniqueNew.length === 0) {
        suppressScrollRef.current = false;
        isPrependingRef.current = false;
        return;
      }

      const merged = [...uniqueNew, ...allCandlesRef.current].sort(
        (a, b) => a.time - b.time,
      );
      allCandlesRef.current = merged;

      // 차트 데이터 재설정
      candleSeriesRef.current.setData(merged.map(toCandlestick));
      volumeSeriesRef.current.setData(merged.map(toVolume));

      // 스크롤 위치 유지: 추가된 캔들 수만큼 오프셋
      if (visibleRange) {
        const offset = uniqueNew.length;
        requestAnimationFrame(() => {
          timeScale.setVisibleLogicalRange({
            from: visibleRange.from + offset,
            to: visibleRange.to + offset,
          });

          // suppress 해제 + isPrepending 해제: 범위 설정이 완료된 후
          requestAnimationFrame(() => {
            suppressScrollRef.current = false;
            // isPrepending은 약간 더 늦게 해제 — React 렌더 사이클을 충분히 넘기기 위해
            setTimeout(() => { isPrependingRef.current = false; }, 100);
          });
        });
      } else {
        setTimeout(() => {
          suppressScrollRef.current = false;
          isPrependingRef.current = false;
        }, 500);
      }
    }, []);

    const fitContent = useCallback(() => {
      suppressScrollRef.current = true;
      chartRef.current?.timeScale().fitContent();
      setTimeout(() => { suppressScrollRef.current = false; }, 500);
    }, []);

    useImperativeHandle(ref, () => ({
      updateCandle,
      addCompletedCandle,
      updateFromTick,
      setData,
      prependData,
      fitContent,
    }));

    return (
      <div
        ref={containerRef}
        className="w-full rounded-lg overflow-hidden"
        style={{ height }}
      />
    );
  },
);

export default CandlestickChart;
