"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { authApi } from "@/lib/api";
import { communityListUrl, communityReturnUrl, readCommunityListState } from "@/lib/community-navigation";
import type { AuthUser, BoardType } from "@/types/api";
import styles from "./board-menu.module.css";

type Props = { role?: AuthUser["role"] | null };

function Chevron() {
  return <svg className={styles.chevron} width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="m3 4.5 3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

export function BoardMenu(props: Props) {
  return (
    <Suspense fallback={<span className={styles.root}><button className={styles.trigger} type="button" disabled aria-expanded="false">게시판<Chevron /></button></span>}>
      <BoardMenuContent {...props} />
    </Suspense>
  );
}

function BoardMenuContent({ role }: Props) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const location = `${pathname}?${searchParams.toString()}`;
  const previousLocation = useRef(location);
  const menuId = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLElement>(null);
  const pendingFocus = useRef<"first" | "last" | null>(null);
  const [open, setOpen] = useState(false);
  const [sessionRole, setSessionRole] = useState<AuthUser["role"] | null>(null);
  const currentRole = role ?? sessionRole;
  const inCommunity = pathname.startsWith("/community");
  const currentBoard = !inCommunity ? undefined
    : pathname === "/community" || pathname === "/community/new" ? readCommunityListState(searchParams).board
      : searchParams.has("returnTo") ? readCommunityListState(new URL(communityReturnUrl(searchParams), "https://community.invalid").searchParams).board
        : undefined;
  const entries: { board: BoardType; label: string }[] = [
    { board: "FREE", label: "자유게시판" },
    { board: "STOCK", label: "종목토론방" },
    { board: "INQUIRY", label: currentRole === "admin" ? "문의 관리" : currentRole === "user" ? "내 문의" : "문의" }
  ];

  useEffect(() => {
    if (role !== undefined) return;
    let active = true;
    authApi.me().then(user => { if (active) setSessionRole(user.role); }).catch(() => { if (active) setSessionRole(null); });
    return () => { active = false; };
  }, [role]);

  useEffect(() => {
    if (previousLocation.current === location) return;
    previousLocation.current = location;
    setOpen(false);
  }, [location]);

  useLayoutEffect(() => {
    if (!open) return;
    function position() {
      if (!root.current) return;
      const bounds = root.current.getBoundingClientRect();
      const width = Math.min(196, window.innerWidth - 36);
      const left = Math.max(18, Math.min(bounds.left, window.innerWidth - width - 18));
      root.current.style.setProperty("--board-menu-offset", `${left - bounds.left}px`);
    }
    position();
    window.addEventListener("resize", position);
    return () => window.removeEventListener("resize", position);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (pendingFocus.current) {
      const links = menu.current?.querySelectorAll("a");
      const item = pendingFocus.current === "first" ? links?.[0] : links?.[links.length - 1];
      item?.focus();
      pendingFocus.current = null;
    }
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    }
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        trigger.current?.focus();
      }
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  function triggerKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const edge = event.key === "ArrowDown" ? "first" : "last";
    if (open) {
      const links = menu.current?.querySelectorAll("a");
      (edge === "first" ? links?.[0] : links?.[links.length - 1])?.focus();
    } else {
      pendingFocus.current = edge;
      setOpen(true);
    }
  }

  function menuKey(event: KeyboardEvent<HTMLElement>) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const links = Array.from(menu.current?.querySelectorAll("a") ?? []);
    if (!links.length) return;
    event.preventDefault();
    const index = links.findIndex(item => item === document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? links.length - 1
      : (index + (event.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
    links[next].focus();
  }

  return (
    <div ref={root} className={styles.root} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
      <button ref={trigger} type="button" className={styles.trigger} data-active={inCommunity || undefined}
        aria-expanded={open} aria-controls={menuId} onClick={() => setOpen(value => !value)} onKeyDown={triggerKey}>
        게시판<Chevron />
      </button>
      {open ? (
        <div className={styles.popover}>
          <nav ref={menu} id={menuId} className={styles.list} aria-label="게시판 목록" onKeyDown={menuKey}>
            {entries.map(item => <Link key={item.board} className={styles.item}
              href={communityListUrl({ board: item.board, stockCode: "", page: 0 })}
              aria-current={currentBoard === item.board ? "page" : undefined} onClick={() => setOpen(false)}>{item.label}</Link>)}
          </nav>
        </div>
      ) : null}
    </div>
  );
}
