"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const [message, setMessage] = useState("세션을 확인하는 중입니다...");

  useEffect(() => {
    let active = true;

    authApi
      .me()
      .then((user) => {
        if (!active) {
          return;
        }
        router.replace(user.surveyCompleted ? "/dashboard" : "/onboarding/preference");
      })
      .catch(() => {
        if (active) {
          setMessage("활성 세션이 없습니다. 로그인 화면으로 이동합니다...");
          router.replace("/login");
        }
      });

    return () => {
      active = false;
    };
  }, [router]);

  return (
    <div className="auth-wrap">
      <div className="card" style={{ maxWidth: 520 }}>
        <h1>HQA 투자 분석</h1>
        <p className="meta">{message}</p>
      </div>
    </div>
  );
}
