"use client";

import { useEffect } from "react";
import { authApi } from "@/lib/api";

// The backend forgets a session after 30 idle minutes, so a demo page left open logged its user out.
// Any open page now checks the session every 10 minutes. A signed-out check is a harmless 401.
const INTERVAL_MS = 10 * 60 * 1000;

export function SessionKeepAlive() {
  useEffect(() => {
    const timer = setInterval(() => {
      authApi.me().catch(() => undefined);
    }, INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);
  return null;
}
