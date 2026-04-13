export function formatDate(value?: string | null) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

export function titleCaseAgent(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
