package com.hqa.backend.entity.enums;

public enum InvestmentType {
    STABLE,          // 안정형: 원금 손실 거의 불허, 예적금 등 안전자산 위주
    MID_STABLE,      // 안정추구형: 원금 보전 우선, 예적금보다 높은 수익 추구 (채권 비중 높음)
    NEUTRAL,         // 위험중립형: 주식/채권 혼합, 예적금 초과 수익을 위해 일부 위험 감수
    MID_AGGRESSIVE,  // 적극투자형: 높은 수익을 위해 상당한 원금 손실 위험 감수
    AGGRESSIVE       // 공격투자형: 최고 수준의 수익 추구, 원금 손실 위험 매우 높음
}
