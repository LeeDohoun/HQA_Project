import type { Metadata } from "next";
import "./globals.css";
import "@/styles/workspace.css";
import "@/styles/stock.css";
import "@/styles/login.css";
import "@/styles/luna.css";

export const metadata: Metadata = {
  title: "HQA — 나의 투자, 더 선명하게",
  description: "세 AI 전문가의 근거를 살펴보고 모의투자로 검증하는 HQA 투자 워크스페이스"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
