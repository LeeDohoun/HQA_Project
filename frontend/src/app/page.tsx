"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const [message, setMessage] = useState("잠시만요...");

  useEffect(() => {
    let active = true;

    authApi
      .me()
      .then((user) => {
        if (!active) return;
        router.replace(user.surveyCompleted ? "/dashboard" : "/onboarding/preference");
      })
      .catch(() => {
        if (active) {
          setMessage("로그인 화면으로 이동할게요...");
          router.replace("/login");
        }
      });

    return () => {
      active = false;
    };
  }, [router]);

  return (
    <div className="auth-wrap">
      <div className="auth-card" style={{ textAlign: "center", alignItems: "center" }}>
        <div className="auth-brand">
          <span className="auth-brand-mark">H</span>
          <span>HQA</span>
        </div>
        <p className="meta">{message}</p>
      </div>
    </div>
  );
}
