import type { BacktestComparison, BacktestComparisonBundle } from "@/types/backtesting";

const AI_COMPARISON_JSON = "/backtesting/ai-strategy-comparison.json";
const AI_COMPARISON_REPORT = "/backtesting/ai-strategy-comparison-report.md";

async function readStaticAsset<T>(path: string, parse: (response: Response) => Promise<T>): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`백테스트 결과를 불러오지 못했습니다. (${response.status})`);
  }
  return parse(response);
}

export async function loadAiBacktestComparison(): Promise<BacktestComparisonBundle> {
  const [comparison, reportMarkdown] = await Promise.all([
    readStaticAsset<BacktestComparison>(AI_COMPARISON_JSON, (response) => response.json()),
    readStaticAsset<string>(AI_COMPARISON_REPORT, (response) => response.text())
  ]);

  return {
    ...comparison,
    reportMarkdown
  };
}
