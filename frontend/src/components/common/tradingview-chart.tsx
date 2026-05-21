"use client";

import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Candle } from "@/types/api";

type Props = {
  candles: Candle[];
  // 일/주/월봉이면 KIS가 자정 UTC+09:00 기준 epoch을 주므로 BusinessDay 포맷이 더 자연스럽다.
  // 그래도 분봉이 섞일 수 있으니 timeframe 단위로 toggle.
  timeframe: string;
  // 사용자가 차트를 좌측으로 스크롤하여 가시 영역의 시작이 현재 데이터의 좌측 절반을 넘으면 호출.
  // 부모는 has_more 가드 + 페이지네이션 fetch를 책임진다.
  onScrolledPastHalfLeft?: () => void;
};

function isDailyOrCoarser(timeframe: string) {
  return /^(1d|1w|1M|1y)$/.test(timeframe);
}

export function TradingViewChart({ candles, timeframe, onScrolledPastHalfLeft }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  // 콜백을 ref에 담아두면 prop 변경 시마다 chart 리스너를 떼었다 붙일 필요 없음.
  const halfLeftCbRef = useRef(onScrolledPastHalfLeft);
  halfLeftCbRef.current = onScrolledPastHalfLeft;
  // 현재 로드된 캔들 개수도 ref로 — 콜백이 구독 시점의 stale 값을 잡지 않도록.
  const candleCountRef = useRef(candles.length);
  candleCountRef.current = candles.length;

  // 차트 생성 — 컨테이너 라이프사이클과 1:1.
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#cbd5e1",
        fontSize: 11
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.08)" },
        horzLines: { color: "rgba(148, 163, 184, 0.08)" }
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: "rgba(148, 163, 184, 0.2)",
        scaleMargins: { top: 0.1, bottom: 0.25 }
      },
      timeScale: {
        borderColor: "rgba(148, 163, 184, 0.2)",
        // 분봉일 때는 HH:MM, 일봉 이상이면 라이브러리 기본 포맷.
        timeVisible: true,
        secondsVisible: false
      },
      autoSize: true
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#ef4444",       // 한국 관행: 상승 빨강
      downColor: "#3b82f6",     // 한국 관행: 하락 파랑
      borderUpColor: "#ef4444",
      borderDownColor: "#3b82f6",
      wickUpColor: "#ef4444",
      wickDownColor: "#3b82f6"
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume"
    });
    // 거래량 시리즈는 별도 가격 스케일을 차트 하단 25%에 배치.
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 }
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    // 가시 영역 변경을 구독 — from이 현재 데이터 길이의 절반 미만이면 좌측 절반 진입으로 판단.
    // (Lightweight Charts는 from이 음수면 데이터 좌측 끝을 넘어선 영역까지 본다는 뜻.)
    const handleVisibleRange = (range: { from: number; to: number } | null) => {
      if (!range) return;
      const count = candleCountRef.current;
      if (count <= 0) return;
      if (range.from < count / 2) {
        halfLeftCbRef.current?.();
      }
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handleVisibleRange);

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handleVisibleRange);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, []);

  // 첫 로드(또는 종목/timeframe 변경)인지 추적 — prepend는 fit하지 않는다.
  const lastSeedKeyRef = useRef<string | null>(null);

  // 데이터 주입 — candles/timeframe 변경 시.
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const volumeSeries = volumeSeriesRef.current;
    const chart = chartRef.current;
    if (!candleSeries || !volumeSeries || !chart) return;

    // Lightweight Charts는 time이 오름차순으로 정렬되어 있어야 throw 하지 않는다.
    const sorted = [...candles].sort((a, b) => a.time - b.time);
    const coarse = isDailyOrCoarser(timeframe);

    const candleData = sorted.map((c) => ({
      time: c.time as unknown as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close
    }));

    const volumeData = sorted.map((c) => ({
      time: c.time as unknown as Time,
      value: c.volume,
      // 봉의 방향에 맞춰 색칠.
      color: c.close >= c.open ? "rgba(239, 68, 68, 0.45)" : "rgba(59, 130, 246, 0.45)"
    }));

    candleSeries.setData(candleData);
    volumeSeries.setData(volumeData);

    // 타임스케일 포맷을 timeframe 단위로 살짝 조정.
    chart.timeScale().applyOptions({
      timeVisible: !coarse,
      secondsVisible: false
    });

    // timeframe이 바뀌었거나 첫 진입이면 fit, 같은 시드에서 prepend로 길이만 늘었다면 fit 안 함
    // (사용자가 보던 영역이 우측 끝으로 튕기는 것을 방지).
    const seedKey = `${timeframe}:${sorted[0]?.time ?? ""}`;
    const firstSeedForTimeframe = lastSeedKeyRef.current?.split(":")[0] !== timeframe;
    if (firstSeedForTimeframe) {
      chart.timeScale().fitContent();
    }
    lastSeedKeyRef.current = seedKey;
  }, [candles, timeframe]);

  return (
    <div
      ref={containerRef}
      className="tv-chart-host"
      style={{ width: "100%", height: "100%" }}
    />
  );
}

// 일부 콜러가 epoch seconds 외의 다른 포맷을 줄 수 있을 때를 대비한 안전 캐스트.
export type { UTCTimestamp };
