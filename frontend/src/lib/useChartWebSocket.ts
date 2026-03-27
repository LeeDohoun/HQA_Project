// 파일: frontend/src/lib/useChartWebSocket.ts
/**
 * 실시간 차트 WebSocket 훅
 *
 * 백엔드 WebSocket에 연결하여 실시간 틱 & 캔들 데이터를 수신합니다.
 * 자동 재연결, 구독 관리, 연결 상태 추적 기능을 제공합니다.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { CandleData } from '@/lib/api';

// ── 타입 정의 ──

// CandleData는 api.ts에서 import (단일 정의)
export type { CandleData } from '@/lib/api';

export interface TickData {
  stock_code: string;
  price: number;
  volume: number;
  cumulative_volume: number;
  change_rate: number;
  trade_type: string;
  timestamp: string;
  open: number;
  high: number;
  low: number;
}

type WSMessage =
  | { type: 'tick'; data: TickData }
  | { type: 'candle_update'; timeframe: string; data: CandleDict }
  | { type: 'candle_complete'; timeframe: string; data: CandleDict }
  | { type: 'subscribed'; stock_code: string; timeframe: string }
  | { type: 'unsubscribed'; timeframe: string }
  | { type: 'error'; message: string }
  | { type: 'pong' };

interface CandleDict {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  complete: boolean;
}

// ── 훅 옵션 ──

interface UseChartWebSocketOptions {
  /** 캔들 업데이트 콜백 (미완성 캔들 실시간 업데이트) */
  onCandleUpdate?: (candle: CandleData) => void;
  /** 캔들 완성 콜백 (캔들이 닫힐 때) */
  onCandleComplete?: (candle: CandleData) => void;
  /** 틱 콜백 */
  onTick?: (tick: TickData) => void;
  /** 에러 콜백 */
  onError?: (message: string) => void;
}

// ── 유틸 함수 ──

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';

function candleDictToData(d: CandleDict): CandleData {
  return {
    time: Math.floor(new Date(d.timestamp).getTime() / 1000),
    open: d.open,
    high: d.high,
    low: d.low,
    close: d.close,
    volume: d.volume,
    complete: d.complete,
  };
}

// ── 훅 ──

export function useChartWebSocket(
  stockCode: string | null,
  timeframe: string,
  options: UseChartWebSocketOptions = {},
) {
  const [connected, setConnected] = useState(false);
  const [lastTick, setLastTick] = useState<TickData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelayRef = useRef(1000);

  // 콜백 refs (re-render 방지)
  const optionsRef = useRef(options);
  optionsRef.current = options;

  // stockCode와 timeframe을 ref로 추적 (connect 함수가 최신값 참조)
  const stockCodeRef = useRef(stockCode);
  stockCodeRef.current = stockCode;
  const timeframeRef = useRef(timeframe);
  timeframeRef.current = timeframe;

  const connect = useCallback(() => {
    const code = stockCodeRef.current;
    if (!code) return;

    // 기존 재연결 타이머 정리
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    // 기존 연결 정리
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    const url = `${WS_BASE}/api/v1/charts/ws/${code}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      // 이 WebSocket이 여전히 현재 활성 인스턴스인지 확인
      // (React Strict Mode 이중 마운트 대응)
      if (wsRef.current !== ws) return;

      setConnected(true);
      setError(null);
      reconnectDelayRef.current = 1000; // 리셋

      // 타임프레임 구독
      ws.send(JSON.stringify({
        action: 'subscribe',
        timeframe: timeframeRef.current,
      }));
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return;

      try {
        const msg: WSMessage = JSON.parse(event.data);

        switch (msg.type) {
          case 'tick':
            setLastTick(msg.data);
            optionsRef.current.onTick?.(msg.data);
            break;

          case 'candle_update': {
            const candle = candleDictToData(msg.data);
            optionsRef.current.onCandleUpdate?.(candle);
            break;
          }

          case 'candle_complete': {
            const candle = candleDictToData(msg.data);
            optionsRef.current.onCandleComplete?.(candle);
            break;
          }

          case 'error':
            setError(msg.message);
            optionsRef.current.onError?.(msg.message);
            break;

          case 'subscribed':
            // 구독 확인
            break;

          case 'pong':
            break;
        }
      } catch (e) {
        console.error('WebSocket 메시지 파싱 오류:', e);
      }
    };

    ws.onclose = () => {
      // 핵심 수정: 이미 교체된(stale) WebSocket의 onclose는 무시
      if (wsRef.current !== ws) return;

      setConnected(false);

      // 자동 재연결 (stockCode가 아직 존재할 때만)
      if (stockCodeRef.current) {
        const delay = reconnectDelayRef.current;
        reconnectTimerRef.current = setTimeout(() => {
          reconnectDelayRef.current = Math.min(delay * 2, 30000);
          connect();
        }, delay);
      }
    };

    ws.onerror = () => {
      if (wsRef.current !== ws) return;
      setError('WebSocket 연결 오류');
    };
  }, []); // 의존성 없음 — refs로 최신값 참조

  // stockCode 또는 timeframe 변경 시 재연결
  useEffect(() => {
    if (stockCode) {
      reconnectDelayRef.current = 1000; // 새 연결은 딜레이 리셋
      connect();
    } else {
      // stockCode가 null이면 정리
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setConnected(false);
      setLastTick(null);
    }

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        const staleWs = wsRef.current;
        wsRef.current = null; // ref를 먼저 null로 → onclose에서 stale 판정
        staleWs.close();
      }
    };
  }, [stockCode, timeframe, connect]);

  // Ping 유지 (30초마다)
  useEffect(() => {
    const interval = setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ action: 'ping' }));
      }
    }, 30000);

    return () => clearInterval(interval);
  }, []);

  return { connected, lastTick, error };
}
