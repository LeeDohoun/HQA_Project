import type { BoardType, PostAuthor } from "@/types/api";
import styles from "./author-badge.module.css";

export function AuthorBadge({ author, board }: { author: PostAuthor; board: BoardType }) {
  return <span className={styles.author} title={`아이디: ${author.userId}`}>
    <span className={styles.name}>{author.nickname}</span>
    {author.level > 0 && author.title && board !== "INQUIRY"
      ? <span className={styles.rank} data-board={board}>Lv.{author.level} · {author.title}</span> : null}
  </span>;
}
