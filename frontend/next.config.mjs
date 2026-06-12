/** @type {import('next').NextConfig} */
const isProd = process.env.NODE_ENV === "production";
const backendProxyTarget = process.env.BACKEND_PROXY_TARGET || "http://localhost:8000";

// Production-only security headers. In dev we leave http://localhost alone.
const securityHeaders = isProd
  ? [
      {
        key: "Strict-Transport-Security",
        value: "max-age=31536000; includeSubDomains"
      },
      {
        key: "Content-Security-Policy",
        value: "upgrade-insecure-requests"
      },
      {
        key: "X-Content-Type-Options",
        value: "nosniff"
      },
      {
        key: "Referrer-Policy",
        value: "strict-origin-when-cross-origin"
      }
    ]
  : [];

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendProxyTarget}/api/:path*`
      },
      {
        source: "/actuator/:path*",
        destination: `${backendProxyTarget}/actuator/:path*`
      }
    ];
  },
  async headers() {
    if (!isProd) return [];
    return [
      {
        source: "/:path*",
        headers: securityHeaders
      }
    ];
  }
};

export default nextConfig;
