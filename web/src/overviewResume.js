function timestampOf(session) {
  const value = session?.updated_at || session?.created_at;
  const timestamp = Date.parse(value || "");
  return Number.isFinite(timestamp) ? timestamp : 0;
}

/**
 * The overview resumes the work touched most recently. A running session wins,
 * but if more than one is live the newest activity still decides which one is
 * shown. The final id comparison keeps malformed or tied legacy rows stable.
 */
export function selectResumeSession(sessions) {
  const rows = Array.isArray(sessions) ? sessions.filter(Boolean) : [];
  if (rows.length === 0) return null;

  return [...rows].sort((left, right) => {
    const running = Number(Boolean(right.is_running)) - Number(Boolean(left.is_running));
    if (running !== 0) return running;
    const recent = timestampOf(right) - timestampOf(left);
    if (recent !== 0) return recent;
    return String(right.id || "").localeCompare(String(left.id || ""));
  })[0];
}
