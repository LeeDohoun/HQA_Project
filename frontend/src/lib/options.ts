export const investmentGoalOptions = [
  ["RETIREMENT", "은퇴 준비"],
  ["HOME_PURCHASE", "주택 마련"],
  ["VEHICLE_PURCHASE", "차량 구매"],
  ["DEBT_REPAYMENT", "부채 상환"],
  ["EDUCATION_FUND", "교육 자금"],
  ["EMERGENCY_FUND", "비상 자금"],
  ["TRAVEL", "여행 자금"],
  ["WEDDING", "결혼 자금"],
  ["BUSINESS_STARTUP", "창업 자금"],
  ["ASSET_GROWTH", "자산 증식"],
  ["PASSIVE_INCOME", "수동 소득 창출"],
  ["TAX_OPTIMIZATION", "절세"],
  ["DONATION_FUND", "기부 자금"],
  ["OTHER", "기타"]
] as const;

export const investmentExperienceOptions = [
  ["NONE", "경험 없음"],
  ["BEGINNER", "1년 미만"],
  ["INTERMEDIATE", "1년~3년"],
  ["EXPERIENCED", "3년~5년"],
  ["EXPERT", "5년 이상"]
] as const;

export const investmentTypeOptions = [
  ["STABLE", "안정형"],
  ["MID_STABLE", "안정 추구형"],
  ["NEUTRAL", "균형형"],
  ["MID_AGGRESSIVE", "성장 추구형"],
  ["AGGRESSIVE", "공격형"]
] as const;

export const volatilityToleranceOptions = [
  ["VERY_LOW", "매우 낮음"],
  ["LOW", "낮음"],
  ["MEDIUM", "보통"],
  ["HIGH", "높음"],
  ["VERY_HIGH", "매우 높음"]
] as const;

export const lossActionOptions = [
  ["SELL_IMMEDIATELY", "즉시 매도"],
  ["HOLD", "보유 유지"],
  ["BUY_MORE", "추가 매수"],
  ["SEEK_ADVICE", "전문가 상담"]
] as const;

export const lossToleranceOptions = [
  ["LEVEL_1", "0%~10%"],
  ["LEVEL_2", "10%~30%"],
  ["LEVEL_3", "30%~50%"],
  ["LEVEL_4", "50%~70%"],
  ["LEVEL_5", "70%~90%"],
  ["LEVEL_6", "90%~100%"]
] as const;

export const occupationTypeOptions = [
  ["EMPLOYEE", "회사원"],
  ["BUSINESS_OWNER", "자영업자"],
  ["FREELANCER", "프리랜서"],
  ["PUBLIC_SERVANT", "공무원"],
  ["PROFESSIONAL", "전문직"],
  ["FINANCE_WORKER", "금융업 종사자"],
  ["RESEARCHER", "연구직"],
  ["INFLUENCER", "인플루언서"],
  ["STUDENT", "학생"],
  ["HOMEMAKER", "주부"],
  ["RETIRED", "은퇴자"],
  ["UNEMPLOYED", "무직"],
  ["OTHER", "기타"]
] as const;
