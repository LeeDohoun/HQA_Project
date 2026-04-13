export type ApiError = Error & {
  status?: number;
  payload?: unknown;
};

export type AuthUser = {
  id: string;
  userId: string;
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
  estimatedTimeSeconds: number;
};

export type StockInfo = {
  name: string;
  code: string;
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
