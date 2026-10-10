import Link from "next/link";

const experts = [
  { no: "01", name: "Analyst", title: "숫자 너머의 맥락까지.", text: "공시와 뉴스를 읽고, 기업을 움직이는 변화와 투자 근거를 살펴봐요.", mark: "✳", color: "lilac" },
  { no: "02", name: "Quant", title: "느낌 대신, 데이터로.", text: "재무와 정량 지표를 함께 비교해 기업의 가치를 다른 각도에서 확인해요.", mark: "▥", color: "lime" },
  { no: "03", name: "Chartist", title: "흐름 속에서 찾는 단서.", text: "가격과 거래량의 움직임에서 추세를 읽고, 기술적 관점을 더해요.", mark: "↗", color: "peach" }
];

export default function HomePage() {
  return (
    <div className="luna-landing">
      <a className="skip-link" href="#main">본문으로 이동</a>
      <header className="landing-nav">
        <Link href="/" className="luna-wordmark" aria-label="HQA 홈"><span className="luna-symbol" aria-hidden="true" />HQA<span className="wordmark-dot">.</span></Link>
        <nav aria-label="주요 메뉴"><a href="#how-it-works">HQA의 방식</a><Link href="/backtesting/ai">백테스트</Link><Link href="/faq">궁금한 점</Link></nav>
        <Link href="/login" className="landing-login">로그인 <span aria-hidden="true">↗</span></Link>
      </header>
      <main id="main">
        <section className="landing-hero">
          <div className="hero-copy">
            <p className="luna-eyebrow"><span /> A LITTLE CLARITY. A BETTER MOVE.</p>
            <h1>투자의 소음은 낮추고,<br />나의 <span className="hero-highlight">확신은 선명하게<svg viewBox="0 0 460 20" aria-hidden="true"><path d="M4 14 Q220 -5 454 10" /></svg></span>.</h1>
            <p className="hero-description">혼자 고민하던 투자에, 세 가지 새로운 시선.<br />AI의 분석 근거를 읽고 모의투자로 나만의 판단을 만들어보세요.</p>
            <div className="hero-actions"><Link className="luna-cta" href="/signup">나의 HQA 시작하기 <span>↗</span></Link><Link className="luna-text-link" href="/dashboard">워크스페이스 열기 <span>→</span></Link></div>
            <p className="hero-footnote">세 AI 전문가의 분석 · 한국 주식 · 모의투자 전용</p>
          </div>
          <div className="hero-art" aria-label="AI 분석 과정 소개">
            <div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" />
            <div className="art-caption">YOUR NEXT PERSPECTIVE</div>
            <div className="luna-sculpture"><div className="sculpture-hole" /></div>
            <div className="floating-note note-top"><span className="note-icon">✳</span><div><small>혼자보다 넓게</small><strong>세 전문가의 시선</strong></div><span className="note-check">↗</span></div>
            <div className="floating-note note-bottom"><span className="note-icon dark">↗</span><div><small>결론보다 중요한 건</small><strong>설명할 수 있는 근거</strong></div></div>
            <span className="art-coordinate">37.5665° N &nbsp; 126.9780° E</span>
          </div>
        </section>
        <section className="landing-manifesto"><span>LESS NOISE.<br /><b>MORE PERSPECTIVE.</b></span><p>누군가의 정답을 따르기보다,<br /><strong>내가 이해하는 투자를 시작할 시간.</strong></p><a href="#how-it-works" aria-label="HQA의 방식 알아보기">↓</a></section>
        <section id="how-it-works" className="landing-section">
          <div className="landing-section-head"><div><p className="luna-eyebrow">THREE MINDS, ONE CLEARER VIEW</p><h2>같은 종목, 다른 시선.<br />판단의 깊이가 달라져요.</h2></div><p>좋은 신호만 모으지 않아요.<br />서로 다른 근거와 주의할 점을 함께 보여드려요.</p></div>
          <div className="expert-grid">{experts.map(expert => <article className={`expert-card ${expert.color}`} key={expert.name}><div className="expert-top"><span>{expert.no} / AI RESEARCH</span><span className="expert-mark" aria-hidden="true">{expert.mark}</span></div><p className="expert-name">{expert.name}</p><h3>{expert.title}</h3><p>{expert.text}</p></article>)}</div>
        </section>
        <section className="landing-workflow"><div><p className="luna-eyebrow">FROM CURIOSITY TO CLARITY</p><h2>관심에서 분석으로.<br />분석에서 나의 기준으로.</h2><Link className="luna-cta" href="/signup">첫 관심 종목 담기 <span>↗</span></Link></div><ol>{[{title:"궁금한 종목을 담아요",text:"한국 주식을 검색하고 나만의 워치리스트를 만들어요."},{title:"AI의 생각을 펼쳐봐요",text:"전문가별 분석, 데이터 품질, 누락된 근거까지 확인해요."},{title:"모의투자로 검증해요",text:"PAPER 계좌를 연결하고 내 위험 설정에 맞는 매매 흐름을 확인해요."}].map((step,i)=><li key={step.title}><span>0{i+1}</span><div><h3>{step.title}</h3><p>{step.text}</p></div></li>)}</ol></section>
        <section className="landing-finale"><span className="finale-star" aria-hidden="true">✳</span><p className="luna-eyebrow">MAKE ROOM FOR YOUR OWN JUDGEMENT</p><h2>조금 더 아는 나의 투자.<br />오늘부터, HQA.</h2><Link className="luna-cta light" href="/signup">함께 시작하기 <span>↗</span></Link></section>
      </main>
      <footer className="landing-footer"><Link href="/" className="luna-wordmark">HQA</Link><p>HQA Project · AI 투자 리서치 워크스페이스<br />AI 분석은 수익을 보장하지 않으며, 현재 서비스는 모의투자 전용입니다.</p><div><Link href="/disclaimer">투자 유의사항</Link><Link href="/faq">FAQ</Link></div></footer>
    </div>
  );
}
