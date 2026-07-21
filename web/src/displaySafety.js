const WINDOWS_ABSOLUTE_PATH = /(?:[A-Za-z]:\\|\\\\)[^\r\n`"']+/g;
const POSIX_SERVER_PATH = /(^|[\s(`"'=,:])\/(?:home|srv|var|tmp|opt|Users|data|app|workspace)(?:\/[^\s`"'():),]+)+/gm;
const BEARER_CREDENTIAL = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const CREDENTIAL_NAME = "(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|credential|signature)";
const NAMED_CREDENTIAL = new RegExp(`(\\b${CREDENTIAL_NAME}\\b\\s*[:=]\\s*)(?:"[^"]*"|'[^']*'|[^\\s,;}]+)`, "gi");
const QUOTED_NAMED_CREDENTIAL = new RegExp(`((?:"|')${CREDENTIAL_NAME}(?:"|')\\s*:\\s*)(?:"[^"]*"|'[^']*'|[^\\s,;}]+)`, "gi");
const KEY_LIKE_VALUE = /\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b/g;
const FILE_URL = /\bfile:\/\/\/[^\s`"']+/gi;
const SENSITIVE_KEY = /(?:authorization|cookie|password|secret|token|api[_-]?key|credential|signature)/i;

export function sanitizeForDisplay(value, seen = new WeakSet()) {
  if (typeof value === "string") return safeVisibleText(value);
  if (value === null || value === undefined || typeof value !== "object") return value;
  if (seen.has(value)) return "[unavailable]";
  seen.add(value);
  if (Array.isArray(value)) return value.map((item) => sanitizeForDisplay(item, seen));
  return Object.fromEntries(Object.entries(value).map(([key, child]) => [
    key,
    SENSITIVE_KEY.test(key) ? "[REDACTED]" : sanitizeForDisplay(child, seen),
  ]));
}

export function safeVisibleText(value) {
  return String(value ?? "")
    .replace(FILE_URL, "[server path]")
    .replace(WINDOWS_ABSOLUTE_PATH, "[server path]")
    .replace(POSIX_SERVER_PATH, "$1[server path]")
    .replace(BEARER_CREDENTIAL, "Bearer [REDACTED]")
    .replace(QUOTED_NAMED_CREDENTIAL, "$1\"[REDACTED]\"")
    .replace(NAMED_CREDENTIAL, "$1[REDACTED]")
    .replace(KEY_LIKE_VALUE, "[REDACTED]");
}
