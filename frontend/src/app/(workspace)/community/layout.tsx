import type { ReactNode } from "react";
import styles from "./theme.module.css";

export default function CommunityLayout({ children }: { children: ReactNode }) {
  return <div className={styles.community}>{children}</div>;
}
