// 파일: frontend/src/components/TradingViewWidget.tsx
'use client';

/**
 * TradingView Advanced Chart 위젯 컴포넌트
 *
 * TradingView의 무료 임베드 위젯을 사용하여 실시간 차트를 렌더링합니다.
 * KRX 한국 주식을 지원합니다 (예: KRX:005930 = 삼성전자).
 */

import { useEffect, useRef, memo } from 'react';

export interface TradingViewWidgetProps {
  /** 종목코드 (6자리, e.g. "005930") */
  stockCode: string;
  /** 차트 높이 */
  height?: number;
  /** 다크 모드 */
  dark?: boolean;
  /** 기본 타임프레임 (1, 3, 5, 15, 30, 60, D, W, M) */
  interval?: string;
  /** 종목 변경 허용 */
  allowSymbolChange?: boolean;
}

function TradingViewWidgetInner({
  stockCode,
  height = 600,
  dark = true,
  interval = 'D',
  allowSymbolChange = false,
}: TradingViewWidgetProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // 기존 위젯 정리
    containerRef.current.innerHTML = '';

    const widgetContainer = document.createElement('div');
    widgetContainer.className = 'tradingview-widget-container__widget';
    widgetContainer.style.height = `${height}px`;
    widgetContainer.style.width = '100%';
    containerRef.current.appendChild(widgetContainer);

    const script = document.createElement('script');
    script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
    script.type = 'text/javascript';
    script.async = true;
    script.innerHTML = JSON.stringify({
      symbol: `KRX:${stockCode}`,
      interval,
      timezone: 'Asia/Seoul',
      theme: dark ? 'dark' : 'light',
      style: '1', // 캔들스틱
      locale: 'kr',
      allow_symbol_change: allowSymbolChange,
      save_image: true,
      calendar: false,
      hide_volume: false,
      support_host: 'https://www.tradingview.com',
      width: '100%',
      height,
    });

    containerRef.current.appendChild(script);

    return () => {
      if (containerRef.current) {
        containerRef.current.innerHTML = '';
      }
    };
  }, [stockCode, height, dark, interval, allowSymbolChange]);

  return (
    <div
      ref={containerRef}
      className="tradingview-widget-container w-full rounded-lg overflow-hidden"
    />
  );
}

const TradingViewWidget = memo(TradingViewWidgetInner);
export default TradingViewWidget;
