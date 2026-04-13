type StatusPillProps = {
  label: string;
  tone?: "neutral" | "good" | "warn" | "bad";
};

export function StatusPill({ label, tone = "neutral" }: StatusPillProps) {
  return <span className={`status-pill status-pill-${tone}`}>{label}</span>;
}
