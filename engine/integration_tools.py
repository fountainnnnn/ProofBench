"""The tools the integration agent may use, and the vault behind them.

The agent used to run a fixed pipeline: decide once, search once, read up to
four pages, answer. It could describe every setting in this deployment and
change none of them, so "set it for me" could only ever produce prose telling
the operator to go and do it themselves.

This module is the other half. It gives the agent bounded tools for the
settings an operator can already change in the same browser tab, so the agent
acquires no authority its caller does not already hold, and it withholds the
two things that would be new authority: writing application source and
activating a connector. Those remain explicit operator actions, exactly as
before.

Every write is delegated to the caller through :class:`Actions`. The engine
never touches tenant storage; the server hands down a bound implementation that
re-validates each write on the same code path the HTTP endpoints use. A bug
here can therefore waste a tool call, but it cannot store a value the API
itself would have refused.

Secrets never reach the model. An operator who pastes a key into the chat has
put it in the one place the agent must not see, so :class:`SecretVault` lifts it
out before the turn is composed and leaves an opaque reference behind. The agent
asks for the reference to be stored under a variable name; the server resolves
it. That is what makes "set it for me" both possible and safe.
"""
from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlsplit

MAX_TOOL_RESULT_CHARS = 12_000
MAX_SEARCH_RESULTS = 6
MAX_ORDER_ENTRIES = 8

# A reference the model sees in place of a secret. Deliberately unmistakable:
# it must be obvious in a transcript that this is a handle, not a value, and it
# must never be confusable with a real credential.
SECRET_REF_RE = re.compile(r"^pasted_secret_[0-9]{1,3}$")

# Candidate tokens, before the entropy rules below decide which are secrets.
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_\-])([A-Za-z0-9][A-Za-z0-9_\-]{15,199})(?![A-Za-z0-9_\-])")
# Prefixes vendors put on keys. These are vaulted at a shorter length than an
# anonymous token, because "sk-" followed by anything is never a model id.
_KEY_PREFIX_RE = re.compile(
    r"^(?:sk|pk|rk|ak|api|key|tok|token|secret|ghp|gho|glpat|xox[abposr])[-_]", re.IGNORECASE)


def _looks_like_secret(token: str) -> bool:
    """Whether a pasted token should be lifted out before the model sees it.

    Two rules, both deliberately narrow at the edges:

    A vendor key prefix (``sk-``, ``ghp_``, …) is decisive at 20 characters —
    nothing else in this domain is spelled that way.

    Anything else has to be a long unbroken alphanumeric run mixing letters and
    digits. Requiring *unbroken* is what keeps hyphenated identifiers out:
    ``meta-llama/llama-3.1-70b-instruct`` is a value an operator legitimately
    asks to have stored, and vaulting it would replace a model id the agent
    needs to read with a handle it cannot reason about. A key with internal
    hyphens still gets caught by the prefix rule, which is how real ones are
    written.

    The failure modes are not symmetric, so the bar sits where it does on
    purpose: vaulting something harmless costs one confused tool call, and
    missing a real key sends it to a third-party model. Neither rule is a
    licence to relax the other — the agent is also told, in its own
    instructions, never to ask for a key in prose.
    """
    if len(token) >= 20 and _KEY_PREFIX_RE.match(token):
        return True
    return (
        len(token) >= 24
        and token.isalnum()
        and any(ch.isdigit() for ch in token)
        and any(ch.isalpha() for ch in token)
    )


class SecretVault:
    """Operator-pasted secrets, held here under references the model may repeat.

    One vault serves one turn. It is never persisted, never logged, and never
    returned to the client: its whole life is between reading the operator's
    message and resolving a write the agent asked for.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def scrub(self, text: str) -> str:
        """Return `text` with every secret-looking token replaced by a reference.

        Repeating a value reuses its reference, so an operator who pastes the
        same key twice does not get two handles for one secret.
        """
        existing = {value: ref for ref, value in self._values.items()}

        def swap(match: re.Match[str]) -> str:
            token = match.group(1)
            if not _looks_like_secret(token):
                return token
            ref = existing.get(token)
            if ref is None:
                ref = f"pasted_secret_{len(self._values) + 1}"
                self._values[ref] = token
                existing[token] = ref
            return ref

        return _TOKEN_RE.sub(swap, str(text or ""))

    def resolve(self, ref: str) -> str:
        """The real value behind a reference, or a hard failure."""
        value = self._values.get(str(ref or "").strip())
        if not value:
            raise ValueError(
                "no secret was pasted under that reference; ask the operator to "
                "paste the key and do not guess a value")
        return value

    def __bool__(self) -> bool:
        return bool(self._values)

    @property
    def references(self) -> list[str]:
        return sorted(self._values)


class Actions:
    """The writes the agent may request, refused by default.

    The engine defines the shape and nothing else. A caller that supplies no
    implementation — a test, a script, anything outside the API — gets an agent
    that can read and research but cannot change a deployment, which is the
    right default for a module that would otherwise write tenant state as a side
    effect of being imported.
    """

    def save_credential(self, env: str, value: str) -> str:
        raise NotImplementedError("this deployment did not enable configuration writes")

    def save_setting(self, env: str, value: str) -> str:
        raise NotImplementedError("this deployment did not enable configuration writes")

    def remove_setting(self, env: str) -> str:
        raise NotImplementedError("this deployment did not enable configuration writes")

    def set_scraper_order(self, order: list[str]) -> str:
        raise NotImplementedError("this deployment did not enable configuration writes")

    def environment(self) -> dict[str, str]:
        """The settings snapshot as it stands *now*.

        Re-read after every write, so an agent that stores a key and then asks
        what is configured sees its own effect rather than the state it started
        from. Without this the agent reliably contradicts itself within one turn.
        """
        raise NotImplementedError


def definitions(writable: bool) -> list[dict[str, Any]]:
    """The tool schemas offered to the model for this turn.

    Write tools are omitted entirely rather than offered and refused when the
    caller supplies no :class:`Actions`. A tool the model can see is a tool it
    will plan around, and planning around one that always fails produces a turn
    spent apologising.
    """
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "deployment_state",
                "description": (
                    "What ProofBench implements and how this deployment is configured "
                    "right now: LLM providers and their models, scraping providers and "
                    "the order they are tried in, which credentials are set, and which "
                    "candidate tools have a built-in adapter. Never returns a value. "
                    "Call this before answering anything about what is or is not set up."
                ),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_documentation",
                "description": (
                    "Search the web for a vendor's official documentation. Use only for "
                    "facts about an external service — its endpoints, models, or "
                    "authentication. What ProofBench itself does is in deployment_state."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "A web search query."}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "request_credential",
                "description": (
                    "Ask the operator for a key you cannot store yourself, by naming the "
                    "variable it belongs in. The interface renders a field they paste "
                    "into and stores the value directly; you never see it. Use this "
                    "whenever a key is needed and none has been pasted in this "
                    "conversation. Never ask for a key in prose instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "env": {"type": "string",
                                "description": "The exact variable name, e.g. SCRAPEDO_API_TOKEN."},
                        "label": {"type": "string",
                                  "description": "The provider's display name, e.g. Scrape.do."},
                    },
                    "required": ["env", "label"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_documentation",
                "description": "Fetch the readable text of one https documentation page.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "An https URL."}},
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    if not writable:
        return tools
    tools.extend([
        {
            "type": "function",
            "function": {
                "name": "save_credential",
                "description": (
                    "Store a key the operator has already pasted in this conversation. "
                    "You never see the key itself: pass the pasted_secret_N reference "
                    "that stands in for it. If no such reference appears in the "
                    "conversation, do not call this — say what variable the key belongs "
                    "in and let the operator paste it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "env": {"type": "string",
                                "description": "The exact variable name, e.g. SCRAPEDO_API_TOKEN."},
                        "secret_ref": {"type": "string",
                                       "description": "A pasted_secret_N reference from this conversation."},
                    },
                    "required": ["env", "secret_ref"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_setting",
                "description": (
                    "Set a non-secret provider setting: a MODEL, a BASE_URL, or the "
                    "supervisor provider. Never use this for a credential."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "env": {"type": "string", "description": "e.g. OPENAI_ORCHESTRATOR_MODEL."},
                        "value": {"type": "string", "description": "The literal value to store."},
                    },
                    "required": ["env", "value"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "remove_setting",
                "description": "Delete a stored credential or setting for this deployment.",
                "parameters": {
                    "type": "object",
                    "properties": {"env": {"type": "string", "description": "The variable to clear."}},
                    "required": ["env"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_scraper_order",
                "description": (
                    "Set the order scraping providers are tried in. Names only, most "
                    "preferred first; any provider left out keeps working as a fallback."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order": {"type": "array", "items": {"type": "string"},
                                  "description": "Provider names, e.g. [\"scrapedo\", \"oxylabs\"]."},
                    },
                    "required": ["order"],
                    "additionalProperties": False,
                },
            },
        },
    ])
    return tools


def _order_argument(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("order must be a list of provider names")
    names = [str(item).strip().lower() for item in value[:MAX_ORDER_ENTRIES]]
    cleaned = [name for name in names if re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name)]
    if not cleaned:
        raise ValueError("order named no usable provider")
    return cleaned


def _url_argument(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("url must be a plain https documentation URL")
    return url


class Dispatcher:
    """Runs one tool call and reports what happened.

    It collects the sources the agent read, so the reply can cite them the way
    the old fixed pipeline did, and it records whether anything was actually
    changed — the difference between "here is what to do" and "done", which is
    the only part of the outcome an operator acts on.
    """

    def __init__(
        self,
        *,
        env: dict[str, str],
        vault: SecretVault,
        actions: Actions,
        facts: Callable[[dict[str, str]], str],
        search: Callable[..., list[dict[str, Any]]],
        read: Callable[..., str],
        safe_sources: Callable[[list[dict[str, Any]]], list[dict[str, str]]],
        credential: Callable[[Any], dict[str, str] | None],
        on_progress: Callable[..., None],
    ) -> None:
        self.env = dict(env)
        self.vault = vault
        self.actions = actions
        self._facts = facts
        self._search = search
        self._read = read
        self._safe_sources = safe_sources
        self._credential = credential
        self._report = on_progress
        self.sources: list[dict[str, str]] = []
        self.changes: list[str] = []
        # The last variable the agent asked the operator to fill in. One per
        # turn: the panel renders a single field, and a second request would
        # silently replace the first rather than queue behind it.
        self.credential: dict[str, str] | None = None

    def _remember(self, rows: list[dict[str, str]]) -> None:
        seen = {source["url"] for source in self.sources}
        self.sources.extend(row for row in rows if row["url"] not in seen)

    def _refresh(self) -> None:
        """Adopt the settings as they stand after a write, if the caller can say."""
        try:
            self.env = dict(self.actions.environment())
        except NotImplementedError:
            pass

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute one call and return the text the model reads back.

        A refused or failed tool returns its reason as an ordinary result rather
        than raising. The agent is mid-turn and can recover — asking the
        operator for a key it could not find, or correcting a variable name it
        got wrong — and a raise would throw away a turn that is still salvageable.
        """
        try:
            return self._run(name, arguments)[:MAX_TOOL_RESULT_CHARS]
        except NotImplementedError as exc:
            return f"Refused: {exc}"
        except ValueError as exc:
            return f"Refused: {exc}"
        except Exception as exc:
            self._report(phase="tool_failed", tool=name)
            return f"Failed: {type(exc).__name__}"

    def _run(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "deployment_state":
            self._report(phase="state")
            return self._facts(self.env)

        if name == "search_documentation":
            query = str(arguments.get("query") or "").strip()[:400]
            if not query:
                raise ValueError("query is required")
            self._report(phase="search", query=query)
            rows = self._safe_sources(self._search(query, n=8, env=self.env))[:MAX_SEARCH_RESULTS]
            self._report(phase="found", count=len(rows))
            if not rows:
                return "No results."
            return "\n".join(f"{row['title']} — {row['url']}" for row in rows)

        if name == "request_credential":
            credential = self._credential({"env": arguments.get("env"),
                                           "label": arguments.get("label")})
            if not credential:
                raise ValueError(
                    "that is not a provider credential variable this deployment accepts; "
                    "take the exact name from deployment_state")
            self.credential = credential
            self._report(phase="asking", env=credential["env"])
            return (f"The operator is now being shown a field that stores their key as "
                    f"{credential['env']}. Tell them it is there; do not tell them to set "
                    f"the variable themselves.")

        if name == "read_documentation":
            url = _url_argument(arguments.get("url"))
            title = urlsplit(url).hostname or url
            self._report(phase="read", title=title, url=url)
            try:
                body = str(self._read(url, env=self.env) or "").strip()
            except Exception:
                self._report(phase="read_failed", title=title, url=url)
                return "That page could not be retrieved."
            if not body:
                self._report(phase="read_empty", title=title, url=url)
                return "That page returned nothing readable."
            self._report(phase="read_done", title=title, url=url, chars=len(body))
            self._remember([{"title": title, "url": url}])
            return body

        if name == "save_credential":
            env_name = str(arguments.get("env") or "").strip().upper()
            ref = str(arguments.get("secret_ref") or "").strip()
            if not SECRET_REF_RE.fullmatch(ref):
                raise ValueError(
                    "secret_ref must be a pasted_secret_N reference from this conversation")
            # Resolved here and handed straight to the caller. The value exists
            # in this process for the length of one call and is never returned,
            # reported, or written back into the conversation.
            result = self.actions.save_credential(env_name, self.vault.resolve(ref))
            self._report(phase="wrote", env=env_name)
            self.changes.append(f"Saved {env_name}")
            self._refresh()
            return result

        if name == "save_setting":
            env_name = str(arguments.get("env") or "").strip().upper()
            value = str(arguments.get("value") or "").strip()
            if not value:
                raise ValueError("value is required")
            result = self.actions.save_setting(env_name, value)
            self._report(phase="wrote", env=env_name)
            self.changes.append(f"Set {env_name}")
            self._refresh()
            return result

        if name == "remove_setting":
            env_name = str(arguments.get("env") or "").strip().upper()
            result = self.actions.remove_setting(env_name)
            self._report(phase="wrote", env=env_name)
            self.changes.append(f"Removed {env_name}")
            self._refresh()
            return result

        if name == "set_scraper_order":
            order = _order_argument(arguments.get("order"))
            result = self.actions.set_scraper_order(order)
            self._report(phase="wrote", env="scraper order")
            self.changes.append("Reordered the scraper chain")
            self._refresh()
            return result

        raise ValueError(f"no tool named {name}")
