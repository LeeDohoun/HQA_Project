// Load .env-fe before Next.js reads env vars, falls back to .env.local
const { config } = require('dotenv');
const path = require('path');
config({ path: path.resolve(__dirname, '..', '.env-fe') });

/** @type {import('next').NextConfig} */
const nextConfig = {
  // FastAPI 백엔드로 API 프록시
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
