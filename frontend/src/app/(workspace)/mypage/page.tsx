"use client";

import Link from "next/link";
import { redirect } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useWorkspace } from "@/components/common/workspace-frame";
import { authApi, profileApi } from "@/lib/api";
import { communityListUrl } from "@/lib/community-navigation";
import type { BoardProgress, MyProfile, UserPreference } from "@/types/api";
import styles from "./page.module.css";

const number = (value: number) => new Intl.NumberFormat("ko-KR").format(value);
const won = (value: number | null | undefined) => value == null ? "—" : `${number(Math.round(value))}원`;
const date = (value: string) => new Date(value).toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const reasons = { POST_CREATED: "글 작성", COMMENT_CREATED: "댓글 작성", RECOMMEND_RECEIVED: "추천 받기" };

export default function MyPage() {
  const { user, loadingUser, balance, balanceLoading, balanceError, loadBalance, updateUserNickname } = useWorkspace();
  const [profile, setProfile] = useState<MyProfile | null>(null);
  const [preference, setPreference] = useState<UserPreference | null>(null);
  const [preferenceError, setPreferenceError] = useState("");
  const [nickname, setNickname] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (loadingUser || !user) return;
    let active = true;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    profileApi.me(controller.signal).then(data => {
      if (!active) return;
      setProfile(data); setNickname(data.nickname);
    }).catch(error => { if (active) setError(error instanceof Error ? error.message : "프로필을 불러오지 못했습니다."); })
      .finally(() => { if (active) setLoading(false); });
    if (user.surveyCompleted) {
      setPreferenceError("");
      authApi.getPreference().then(data => { if (active) setPreference(data); })
        .catch(error => { if (active) setPreferenceError(error instanceof Error ? error.message : "자산 정보를 불러오지 못했습니다."); });
    }
    return () => { active = false; controller.abort(); };
  }, [user?.id, user?.surveyCompleted, loadingUser, retry]);

  async function saveNickname(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    setSaving(true); setError(""); setMessage("");
    try {
      const updated = await profileApi.updateNickname(nickname.trim());
      setProfile(updated); setNickname(updated.nickname); updateUserNickname(updated.nickname);
      setMessage("닉네임을 변경했습니다. 게시글과 댓글에도 적용됩니다.");
    } catch (error) {
      setError(error instanceof Error ? error.message : "닉네임을 변경하지 못했습니다.");
    } finally { setSaving(false); }
  }

  if (!loadingUser && !user) redirect("/login");
  if (loadingUser || !user) return <main className={styles.page}><p className={styles.muted}>불러오는 중...</p></main>;

  const kisLinked = user.kisConfigured;
  const totalAsset = kisLinked ? balance?.summary?.totalEvalAmount : preference?.totalAssets;

  return (
    <main className={styles.page}>
      <div className={styles.heading}>
        <div><span className={styles.eyebrow}>MY PAGE</span><h1>마이 페이지</h1><p>나의 자산과 게시판 활동을 한눈에 확인하세요.</p></div>
        <button className={styles.secondary} type="button" disabled={loading} onClick={() => { setRetry(value => value + 1); if (kisLinked) void loadBalance(); }}>새로고침</button>
      </div>

      {error ? <p className={styles.alert} role="alert">{error}</p> : null}
      {message ? <p className={styles.notice} role="status">{message}</p> : null}
      {loading && !profile ? <p className={styles.muted}>프로필을 불러오는 중...</p> : null}

      {profile ? (
        <>
          <div className={styles.profileGrid}>
            <section className={styles.card} aria-labelledby="profile-title">
              <h2 id="profile-title">내 프로필</h2>
              <dl className={styles.info}><div><dt>아이디</dt><dd>{profile.userId}</dd></div><div><dt>계정</dt><dd>{user.role === "admin" ? "관리자" : "일반 회원"}</dd></div><div><dt>가입일</dt><dd>{date(profile.createdAt)}</dd></div></dl>
              <form onSubmit={saveNickname} className={styles.form}>
                <label htmlFor="profile-nickname">닉네임</label>
                <div className={styles.inputRow}><input id="profile-nickname" value={nickname} minLength={2} maxLength={30} required onChange={event => setNickname(event.target.value)} /><button className={styles.button} type="submit" disabled={saving || nickname.trim() === profile.nickname}>{saving ? "저장 중..." : "닉네임 저장"}</button></div>
                <small className={styles.muted}>2~30자 · 한글, 영문, 숫자, 공백, . _ - 사용 가능</small>
              </form>
            </section>
            <section className={styles.card} aria-labelledby="points-total-title">
              <h2 id="points-total-title">보유 포인트</h2><p className={styles.big}>{number(profile.totalPoints)}<span>P</span></p>
              <p className={styles.muted}>자유게시판과 종목토론방에서 모은 포인트의 합계입니다. 각 게시판의 레벨과 칭호는 별도로 계산합니다.</p>
              <div className={styles.rules}><span>글 작성 <b>+10P</b></span><span>댓글 작성 <b>+2P</b></span><span>추천 받기 <b>+5P</b></span></div>
            </section>
          </div>

          <section className={styles.section} aria-labelledby="board-progress-title">
            <h2 id="board-progress-title">게시판별 레벨과 칭호</h2>
            <div className={styles.boardGrid}>{profile.boards.map(board => <BoardCard key={board.boardType} board={board} />)}</div>
            <p className={styles.fine}>문의 작성에는 포인트가 지급되지 않습니다. 글·댓글을 삭제하면 관련 포인트가 회수되며, 레벨과 칭호도 현재 포인트에 맞춰 계산됩니다.</p>
          </section>
        </>
      ) : null}

      <section className={styles.section} aria-labelledby="my-assets-title">
        <div className={styles.sectionHeading}><h2 id="my-assets-title">내 자산</h2><Link className={styles.textLink} href="/settings/kis">{kisLinked ? "계좌 설정" : "KIS 계좌 연결"}</Link></div>
        <p className={styles.muted}>{kisLinked ? "연결된 KIS 계좌에서 조회한 잔고입니다." : "투자 성향 설문에 입력한 자산입니다."}</p>
        {(kisLinked ? balanceError : preferenceError) ? <p className={styles.alert} role="alert">{kisLinked ? balanceError : preferenceError}</p> : null}
        <div className={styles.assetGrid}>
          <div className={styles.card}><span className={styles.muted}>{kisLinked ? "계좌 전체 잔고" : "총 자산"}</span><p className={styles.figure}>{kisLinked && balanceLoading ? "불러오는 중..." : won(totalAsset)}</p></div>
          <div className={styles.card}><span className={styles.muted}>{kisLinked ? "예수금" : "월 투자 금액"}</span><p className={styles.figure}>{won(kisLinked ? balance?.summary?.deposit : preference?.monthlyInvestment)}</p></div>
          <div className={styles.card}><span className={styles.muted}>{kisLinked ? "주식 평가 금액" : "투자 유형"}</span><p className={styles.figure}>{kisLinked ? won(balance?.summary?.stockEvalAmount) : preference?.investmentType ?? "—"}</p></div>
        </div>
        {!kisLinked ? <Link className={styles.textLink} href="/onboarding/preference">투자 성향과 자산 정보 수정</Link> : null}
        {kisLinked && balance?.holdings.length ? (
          <div className={styles.tableWrap}><table><caption>보유 종목</caption><thead><tr><th>종목</th><th>수량</th><th>평가 금액</th></tr></thead><tbody>{balance.holdings.map(holding => <tr key={holding.stockCode}><td><Link className={styles.textLink} href={`/stocks/${holding.stockCode}`}>{holding.stockName}</Link></td><td>{number(holding.quantity)}주</td><td>{won(holding.evalAmount)}</td></tr>)}</tbody></table></div>
        ) : null}
      </section>

      {profile ? <section className={styles.section} aria-labelledby="point-history-title">
        <h2 id="point-history-title">최근 포인트 내역</h2>
        {profile.recentEvents.length ? <div className={styles.tableWrap}><table><thead><tr><th>활동</th><th>게시판</th><th>포인트</th><th>일시</th></tr></thead><tbody>{profile.recentEvents.map(event => <tr key={event.id} data-reversed={!!event.reversedAt}><td>{reasons[event.reason]}{event.reversedAt ? <small className={styles.reversed}>회수됨</small> : null}</td><td>{event.boardType === "FREE" ? "자유게시판" : "종목토론방"}</td><td className={styles.pointValue}>+{event.points}P</td><td>{date(event.createdAt)}</td></tr>)}</tbody></table></div> : <div className={styles.empty}>아직 포인트 내역이 없습니다. 게시판에서 첫 활동을 시작해보세요.</div>}
      </section> : null}
    </main>
  );
}

function BoardCard({ board }: { board: BoardProgress }) {
  const free = board.boardType === "FREE";
  const value = board.nextLevelPoints == null ? 1 : board.points - board.currentLevelPoints;
  const max = board.nextLevelPoints == null ? 1 : board.nextLevelPoints - board.currentLevelPoints;
  return <article className={`${styles.card} ${styles.board}`} data-board={board.boardType}>
    <div className={styles.sectionHeading}><h3>{free ? "자유게시판" : "종목토론방"}</h3><Link className={styles.textLink} href={communityListUrl({ board: board.boardType, stockCode: "", page: 0 })}>게시판 이동</Link></div>
    <p className={styles.titleBadge}>Lv.{board.level} · {board.title}</p><p className={styles.figure}>{number(board.points)}<span>P</span></p>
    <progress aria-label={`${free ? "자유게시판" : "종목토론방"} 레벨 진행도`} value={value} max={max} />
    <p className={styles.fine}>{board.nextLevelPoints == null ? "최고 레벨에 도달했습니다." : `다음 레벨까지 ${number(board.pointsToNextLevel)}P · ${number(board.nextLevelPoints)}P에서 레벨업`}</p>
    <div className={styles.activity}><span>글 <b>{number(board.postCount)}</b></span><span>댓글 <b>{number(board.commentCount)}</b></span><span>받은 추천 <b>{number(board.recommendationsReceived)}</b></span></div>
  </article>;
}
