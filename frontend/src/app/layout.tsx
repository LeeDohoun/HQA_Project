import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HQA 투자 분석",
  description: "HQA 투자 분석 서비스를 위한 프런트엔드"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
