"use client";

import { useRouter } from "next/navigation";

export default function DisclaimerPage() {
  const router = useRouter();

  function goBack() {
    if (typeof window !== "undefined" && window.history.length > 1) {
      window.history.back();
    } else {
      router.push("/onboarding/preference?step=kis");
    }
  }

  return (
    <div className="wiz-wrap">
      <div className="wiz-card" style={{ marginTop: 24, textAlign: "left", maxWidth: 720 }}>
        <span className="wiz-emoji" aria-hidden>📜</span>
        <h1 className="wiz-question" style={{ textAlign: "center" }}>
          투자 및 자동매매 면책 동의서
        </h1>
        <p className="wiz-hint" style={{ textAlign: "center" }}>
          서비스를 이용하기 전 반드시 아래 내용을 확인해주세요.
        </p>

        <section style={{ marginTop: 24, lineHeight: 1.7, fontSize: 14, color: "#374151" }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 16 }}>1. 서비스의 성격</h2>
          <p>
            HQA(이하 “회사”)가 제공하는 AI 분석, 추천 종목, 자동매매 기능은 투자 판단을
            보조하기 위한 정보 제공 서비스이며, 투자 권유나 수익 보장이 아닙니다.
            최종 투자 결정과 그에 따른 결과의 책임은 전적으로 이용자 본인에게 있습니다.
          </p>

          <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 16 }}>2. 자동매매 기능에 대한 안내</h2>
          <ul style={{ paddingLeft: 18 }}>
            <li>
              자동매매는 한국투자증권(KIS) OpenAPI를 통해 이용자의 증권 계좌에 직접
              매수/매도 주문을 전송하는 방식으로 동작합니다.
            </li>
            <li>
              네트워크 지연, KIS 서버 점검·장애, 시세 급변, 거래 정지, 시스템 오류 등
              회사의 통제 범위를 벗어난 사유로 주문이 지연·실패·중복 체결될 수 있습니다.
            </li>
            <li>
              AI 모델의 예측은 과거 데이터에 기반하며, 미래 수익률이나 손실 회피를
              보장하지 않습니다. 시장 상황에 따라 손실이 발생할 수 있습니다.
            </li>
            <li>
              자동매매는 이용자가 직접 활성화/비활성화할 수 있으며, 언제든지 설정에서
              KIS 키를 삭제하여 즉시 중단할 수 있습니다.
            </li>
          </ul>

          <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 16 }}>3. 책임의 한계</h2>
          <p>
            이용자는 자동매매로 인해 발생한 모든 손익(원금 손실 포함)이 본인의 책임임에
            동의합니다. 회사는 다음 사유로 인한 손해에 대해 법령에서 허용하는 최대한도
            내에서 책임을 지지 않습니다.
          </p>
          <ul style={{ paddingLeft: 18 }}>
            <li>이용자가 잘못 입력하거나 관리한 KIS 키·계좌 정보로 인한 손해</li>
            <li>KIS, 증권사, 거래소 등 제3자 시스템의 장애 또는 정책 변경</li>
            <li>천재지변, 정전, 통신 두절 등 불가항력적 사유</li>
            <li>이용자가 임의로 설정값을 변경하여 발생한 손익</li>
          </ul>

          <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 16 }}>4. 키 보관과 보안</h2>
          <p>
            이용자가 등록한 KIS App Key·Secret·계좌번호는 AES-256으로 암호화되어 저장되며,
            주문 전송 직전에만 메모리에서 복호화됩니다. 키는 매수·매도 권한만 가지며,
            출금·송금 권한은 부여되지 않습니다.
          </p>

          <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 16 }}>5. 동의의 효력</h2>
          <p>
            본 동의서에 동의하지 않으시는 경우 자동매매 기능을 사용하실 수 없습니다.
            동의 이후에도 언제든지 설정에서 자동매매를 중단하실 수 있으며, 이 경우에도
            본 동의서의 책임 한계 조항은 동의 기간 중 발생한 거래에 대해 유효합니다.
          </p>
        </section>

        <div
          style={{
            marginTop: 36,
            marginBottom: 12,
            display: "flex",
            justifyContent: "center"
          }}
        >
          <button
            type="button"
            onClick={goBack}
            className="wiz-cta"
            style={{
              padding: "14px 32px",
              minWidth: 220,
              fontSize: 15,
              fontWeight: 600
            }}
          >
            ← 온보딩으로 돌아가기
          </button>
        </div>
      </div>
    </div>
  );
}
