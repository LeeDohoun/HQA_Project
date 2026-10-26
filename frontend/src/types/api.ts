export type ApiError = Error & {
  status?: number;
  payload?: unknown;
};

export type AuthUser = {
  id: string;
  userId: string;
  nickname: string;
  firstName: string;
  lastName: string;
  role: "user" | "admin";
  active: boolean;
  kisConfigured: boolean;
  surveyCompleted: boolean;
  createdAt: string;
};

export type AuthResponse = {
  success: boolean;
  message: string;
  user: AuthUser | null;
};

export type SignupRequest = {
  userId: string;
  firstName: string;
  lastName: string;
  password: string;
};

export type LoginRequest = {
  userId: string;
  password: string;
};

export type KisCredentials = {
  kisAppKey: string;
  kisAppSecret: string;
  kisAccountNo: string;
  kisAccountProductCode: string;
  kisIsReal: boolean;
};

export type KisCredentialsStatus = {
  configured: boolean;
  kisAppKeyMasked: string | null;
  kisAccountNoMasked: string | null;
  kisAccountProductCode: string | null;
  kisIsReal: boolean;
};

export type KisVerificationResult = {
  ok: boolean;
  tokenOk: boolean;
  accountOk: boolean;
  stage: "ok" | "token" | "account" | string;
  message: string;
};

export type UserPreference = {
  totalAssets: number;
  monthlyInvestment: number;
  investmentPeriodMonths: number;
  targetReturnRate: number;
  investmentGoal: string;
  investmentExperience: string;
  birthDate: string;
  investmentType: string;
  volatilityTolerance: string;
  lossAction: string;
  leverageAllowed: boolean;
  occupationType: string;
  lossTolerance: string;
  updatedAt?: string;
};

export type StockSearchResult = {
  name: string;
  code: string;
  market: string;
};

export type StockSearchResponse = {
  results: StockSearchResult[];
  total: number;
};

export type WatchlistItem = StockSearchResult & {
  id: string;
  createdAt?: string;
  updatedAt?: string;
};

export type WatchlistResponse = {
  items: WatchlistItem[];
  total: number;
};

export type RealtimePrice = {
  stock: StockInfo;
  currentPrice: number;
  change: number;
  changeRate: number;
  openPrice: number;
  highPrice: number;
  lowPrice: number;
  volume: number;
  marketCap: number | null;
  per: number | null;
  pbr: number | null;
  timestamp: string;
};

export type Holding = {
  stockCode: string;
  stockName: string;
  quantity: number;
  avgPrice: number;
  currentPrice: number;
  evalAmount: number;
  purchaseAmount: number;
  evalProfit: number;
  evalProfitRate: number;
};

export type BalanceSummary = {
  deposit: number;
  totalEvalAmount: number;
  totalPurchaseAmount: number;
  totalEvalProfit: number;
  stockEvalAmount: number;
  netAssetAmount: number;
};

export type Balance = {
  success: boolean;
  holdings: Holding[];
  summary: BalanceSummary;
  source?: string;
  error?: string;
};

export type AiActivityLeader = {
  rank: number;
  theme: string | null;
  themeKey: string | null;
  stockName: string;
  stockCode: string;
  action: string;
  actionCode: string;
  confidence: number | null;
  score: number | null;
  riskLevel: string | null;
  summary: string;
  analystSummary?: string;
  quantScore?: number;
  chartSignal?: string;
  catalysts?: string[];
  returnPct?: number;
};

export type AiActivityResponse = {
  status: string;
  source?: string;
  sourceReport?: string;
  mode?: string;
  executedAt?: string;
  bestTheme?: string | null;
  themeCount?: number;
  leaderCount?: number;
  leaders: AiActivityLeader[];
  message?: string;
};

export type AutoTradeAgentReason = {
  agent: string;
  label: string;
  summary: string;
  verdict: string;
  score: number | null;
};

export type AutoTradeExplanation = {
  signalId: string;
  source: string;
  strategyProfile: string;
  themeName: string | null;
  stockCode: string;
  stockName: string;
  action: string;
  leaderScore: number;
  confidence: number;
  riskLevel: string;
  positionSize: string;
  signalPrice: number | null;
  stopLoss: string;
  reason: string;
  status: string;
  rejectReason: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  executedAt: string | null;
  executionStatus: string | null;
  executionRejectReason: string | null;
  quantity: number | null;
  orderPrice: number | null;
  currentPrice: number | null;
  priceDriftPct: number | null;
  explanationSummary: string;
  catalysts: string[];
  risks: string[];
  agentReasons: AutoTradeAgentReason[];
};

export type AutoTradeExplanationResponse = {
  items: AutoTradeExplanation[];
};

export type NewsItem = {
  stockCode: string | null;
  stockName: string | null;
  title: string;
  summary: string | null;
  source: string | null;
  url: string;
  publishedAt: string | null;
  createdAt: string | null;
};

export type DisclosureItem = {
  stockCode: string | null;
  stockName: string | null;
  reportName: string;
  receiptNo: string | null;
  receiptDate: string | null;
  submitter: string | null;
  url: string;
  createdAt: string | null;
};

export type MarketIndex = {
  code: string;
  name: string;
  current: number;
  change: number;
  changeRate: number;
  sign: string | null;
};

export type MarketIndexResponse = {
  items: MarketIndex[];
  configured: boolean;
  error?: string;
};

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  complete: boolean | null;
};

export type CandleHistory = {
  stockCode: string;
  timeframe: string;
  candles: Candle[];
  hasMore: boolean;
};

export type AnalysisMode = "full" | "quick";
export type AnalysisStatus = "pending" | "running" | "completed" | "failed";

export type AnalysisRequest = {
  stockName: string;
  stockCode: string;
  mode: AnalysisMode;
  maxRetries: number;
};

export type AnalysisTaskResponse = {
  taskId: string;
  status: AnalysisStatus;
  message: string;
  estimatedTimeSeconds: number | null;
};

export type StockInfo = {
  name: string;
  code: string;
};

export type AnalysisHistoryItem = {
  taskId: string;
  stock: StockInfo;
  mode: AnalysisMode;
  status: AnalysisStatus;
  totalScore: number | null;
  action: string | null;
  createdAt: string;
  completedAt: string | null;
};

export type AnalysisHistoryResponse = {
  items: AnalysisHistoryItem[];
  total: number;
  page: number;
  pageSize: number;
};

export type ScoreDetail = {
  agent: string;
  totalScore: number;
  maxScore: number;
  grade: string | null;
  opinion: string | null;
  details: Record<string, unknown>;
};

export type AnalysisResult = {
  taskId: string;
  status: AnalysisStatus;
  stock: StockInfo;
  mode: AnalysisMode;
  scores: ScoreDetail[];
  finalDecision: Record<string, unknown>;
  researchQuality: string | null;
  qualityWarnings: string[];
  createdAt: string;
  completedAt: string | null;
  durationSeconds: number | null;
  errors: Record<string, string>;
};

export type AnalysisProgressEvent = {
  agent: string;
  status: string;
  message: string;
  progress: number;
  timestamp: string;
};

export type AnalysisProgressStoredEvent = {
  type: "progress" | "agent_result" | string;
  data: Record<string, unknown>;
};

export type AnalysisProgressPollResponse = {
  taskId: string;
  status: AnalysisStatus;
  events: AnalysisProgressStoredEvent[];
};

export type BoardType = "FREE" | "STOCK" | "INQUIRY";
export type InquiryStatus = "OPEN" | "RESOLVED" | "REJECTED";

export type PostAuthor = {
  id: string;
  userId: string;
  firstName: string;
  lastName: string;
  nickname: string;
  level: number;
  title: string | null;
};

/** 목록용. 본문(content)은 담기지 않는다 — 서버가 목록에서 제외해 보낸다. */
export type PostSummary = {
  id: string;
  boardType: BoardType;
  title: string;
  stockCode: string | null;
  commentCount: number;
  recommendationCount: number;
  inquiryStatus: InquiryStatus | null;
  author: PostAuthor;
  createdAt: string;
  updatedAt: string;
};

export type PostComment = {
  id: string;
  content: string;
  author: PostAuthor;
  mine: boolean;
  deletable: boolean;
  createdAt: string;
  updatedAt: string;
};

export type CommentPage = {
  items: PostComment[];
  nextCursor: string | null;
  hasMore: boolean;
  totalItems: number;
};

export type PostDetail = {
  id: string;
  boardType: BoardType;
  title: string;
  content: string;
  stockCode: string | null;
  commentCount: number;
  recommendationCount: number;
  recommended: boolean;
  recommendable: boolean;
  author: PostAuthor;
  /** 서버가 판정한 삭제 가능 여부. 프론트에서 1시간/댓글수를 다시 계산하지 않는다. */
  deletable: boolean;
  editable: boolean;
  deleteRequestable: boolean;
  inquiryResolvable: boolean;
  targetPostDeletable: boolean;
  deleteBlockedReason: string | null;
  inquiryStatus: InquiryStatus | null;
  adminReply: string | null;
  targetPostId: string | null;
  comments: PostComment[];
  nextCommentCursor: string | null;
  hasMoreComments: boolean;
  createdAt: string;
  updatedAt: string;
};

export type PostListResult = {
  items: PostSummary[];
  page: number;
  size: number;
  totalItems: number;
  totalPages: number;
};

export type BoardProgress = {
  boardType: Exclude<BoardType, "INQUIRY">;
  points: number;
  level: number;
  title: string;
  currentLevelPoints: number;
  nextLevelPoints: number | null;
  pointsToNextLevel: number;
  postCount: number;
  commentCount: number;
  recommendationsReceived: number;
};

export type PointEvent = {
  id: string;
  boardType: Exclude<BoardType, "INQUIRY">;
  reason: "POST_CREATED" | "COMMENT_CREATED" | "RECOMMEND_RECEIVED";
  points: number;
  postId: string;
  createdAt: string;
  reversedAt: string | null;
};

export type MyProfile = {
  id: string;
  userId: string;
  nickname: string;
  totalPoints: number;
  createdAt: string;
  boards: BoardProgress[];
  recentEvents: PointEvent[];
};
