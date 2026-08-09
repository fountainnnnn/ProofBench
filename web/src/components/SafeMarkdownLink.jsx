import { safeHttpUrl } from "../linkSafety.js";

/* A link in model-authored markdown, rendered only when its href is one we are
   willing to send a person to. Anything else keeps its text and loses its
   navigation rather than disappearing, so the reply still reads correctly.
 *
 * Shared by every surface that renders a model reply — the chat thread and the
 * Settings integration agent — because a link that is unsafe in one of them is
 * unsafe in the other. */
export default function SafeMarkdownLink({ href, children }) {
  const safeHref = safeHttpUrl(href);
  return safeHref ? (
    <a href={safeHref} target="_blank" rel="noreferrer">{children}</a>
  ) : (
    <span>{children}</span>
  );
}
