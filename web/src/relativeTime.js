/* Compact relative timestamp shared by Runs and Datasets: "just now", "5m",
   "3h", "2d", then the locale date once a value is more than a week old. The
   absolute datetime is surfaced on hover by the callers' <time title> wrapper. */
export function relativeTime(value) {
  const d = new Date(value);
  if (!value || Number.isNaN(d.getTime())) return "unknown";
  const secs = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (secs < 45) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${Math.max(1, mins)}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days <= 7) return `${days}d`;
  return d.toLocaleDateString();
}
