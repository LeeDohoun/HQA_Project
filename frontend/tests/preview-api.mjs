// Isolated visual QA fixture. No credentials, persistence, upstream calls or trading.
// Run only when the real backend is stopped: node frontend/tests/preview-api.mjs
import { createServer } from 'node:http';
const stock = { name: '삼성전자', code: '005930', market: 'KOSPI' };
const routes = {
  '/api/v1/auth/me': { id: 'visual-test', user_id: 'visual-test', first_name: '미리보기', last_name: '', role: 'user', active: true, kis_configured: false, survey_completed: true, created_at: '2026-09-20T00:00:00Z' },
  '/api/v1/auth/me/preference': { total_assets: 0, monthly_investment: 0, investment_period_months: 12, target_return_rate: 5, investment_goal: 'WEALTH_GROWTH', investment_experience: 'BEGINNER', birth_date: '2000-01-01', investment_type: 'BALANCED', volatility_tolerance: 'MEDIUM', loss_action: 'HOLD', leverage_allowed: false, occupation_type: 'STUDENT', loss_tolerance: 'MEDIUM' },
  '/api/v1/watchlist': { items: [{ id: 'preview', stock_name: stock.name, stock_code: stock.code, market: stock.market }], total: 1 },
  '/api/v1/stocks/search': { results: [stock], total: 1 },
  '/api/v1/stocks/indices': { items: [], configured: false },
  '/api/v1/trading/status': { enabled: false, ai_status: {} },
  '/api/v1/trading/orders': { orders: [] },
  '/api/v1/trading/ai-activity': { leaders: [] },
  '/api/v1/trading/explanations': { items: [] },
  '/api/v1/analysis/history/list': { items: [], total: 0, page: 1, page_size: 10 },
  '/api/v1/stocks/005930/news': { items: [] },
  '/api/v1/stocks/005930/disclosures': { items: [] }
};
createServer((req, res) => {
  const path = new URL(req.url, 'http://127.0.0.1').pathname;
  const data = req.method === 'GET' ? routes[path] : undefined;
  res.writeHead(data ? 200 : 503, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(data ?? { message: '화면 검증용 서버입니다. 이 기능은 실제 백엔드 연결 후 사용할 수 있어요.' }));
}).listen(8000, '127.0.0.1', () => console.log('VISUAL QA ONLY: http://127.0.0.1:8000 — no orders or AI calls'));
