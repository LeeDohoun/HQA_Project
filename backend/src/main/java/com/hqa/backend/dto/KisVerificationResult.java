package com.hqa.backend.dto;

/**
 * KIS API 자격증명 검증 결과.
 *
 * tokenOk   = /oauth2/tokenP 호출 성공 (App Key/Secret 유효)
 * accountOk = 계좌조회 API 호출 성공 (CANO/ACNT_PRDT_CD 유효)
 * stage     = 어디서 실패했는지 ("token" | "account" | "ok")
 * message   = 사용자에게 보여줄 한 줄 메시지 (실패 사유 등)
 */
public record KisVerificationResult(
        boolean ok,
        boolean tokenOk,
        boolean accountOk,
        String stage,
        String message
) {
}
