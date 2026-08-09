import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Sidebar from "../components/Sidebar.jsx";
import ChatThread from "../components/ChatThread.jsx";
import Composer from "../components/Composer.jsx";
import DirectionCard from "../components/DirectionCard.jsx";
import HeaderActions from "../components/HeaderActions.jsx";
import { BTN_PRIMARY, BTN_SECONDARY } from "../components/ui.jsx";
import {
  RUN_MODE,
  postChat,
  uploadDataset,
  startRun,
  stopRun,
  openEvents,
  listSessions,
  createSession,
  deleteSession,
  getSession,
  getResults,
  listDatasets,
} from "../api.js";
import { resolveDatasetSelection } from "../datasetSelection.js";
import { acquireOperation, releaseOperation } from "../operationGuard.js";
import { authoritativeProvenance, hasAuthoritativeProvenance } from "../provenance.js";
import { safeVisibleText } from "../displaySafety.js";
import { phaseLabel, phaseTone } from "../phaseLabel.js";
import StatusIcon from "../components/StatusIcon.jsx";
import SandboxExecutionPanel from "../components/SandboxExecutionPanel.jsx";

const MAX_LOG_LINES = 400;
const MAX_TRACE_EVENTS = 300;
const RECONNECT_TIMEOUT_MS = 20000;
const STREAM_ACTIVITY_TIMEOUT_MS = 5 * 60 * 1000;

export function buildRunSpec(spec, dataset) {
  if (spec?.benchmark_type === "tool_assessment" || !dataset?.id) return spec;
  return {
    ...spec,
    dataset: { ...(spec.dataset || {}), dataset_id: dataset.id },
  };
}

function coalesceRestoredMessages(messages) {
  return (messages || []).reduce((result, message) => {
    const role = message?.role === "user" ? "user" : "assistant";
    const text = String(message?.text || "");
    const previous = result[result.length - 1];
    if (role === "assistant" && previous?.role === "assistant") {
      previous.text += text;
    } else {
      result.push({ role, text });
    }
    return result;
  }, []);
}

function parseEvent(event) {
  try {
    return JSON.parse(event.data);
  } catch {
    return null;
  }
}

const emptyState = (scope) => ({
  scope,
  messages: [],
  trace: [],
  sandboxLogs: {},
  sandboxFiles: {},
  phaseState: null,
  spec: null,
  results: null,
  report: null,
  // The pending direction confirmation, when the agent gated the opening turn.
  // Cleared the moment the user sends anything, by either button or by typing.
  direction: null,
  // How the backend says the run reached its numbers. Rendered verbatim; the
  // UI never derives an execution claim of its own.
  executionMode: "",
  provenance: null,
  specProvenance: null,
  resultsProvenance: null,
});

export default function Benchmark() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [state, setState] = useState(() => emptyState(0));
  // No phantom default: a dataset chip renders only for a dataset that
  // actually exists (has a server id). Until then the composer shows its
  // attach affordances, which is the honest account of the session's state.
  const [dataset, setDataset] = useState(null);
  const [activeRunId, setActiveRunId] = useState(null);
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [typing, setTyping] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const [uploadingDataset, setUploadingDataset] = useState(false);
  const [datasetError, setDatasetError] = useState(() => searchParams.get("dataset") ? "Verifying dataset access…" : "");
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [followUpOpen, setFollowUpOpen] = useState(false);
  const [sandboxPanelOpen, setSandboxPanelOpen] = useState(false);
  // Carrying on after a run is a property of the session, not of this page
  // load. Without it, closing the tab folded the conversation away again and
  // the user had to reopen it to see replies they had already been given.
  const followUpKey = (id) => `proofbench.followUp.${id}`;
  const rememberFollowUp = (id, open) => {
    if (!id) return;
    try {
      if (open) localStorage.setItem(followUpKey(id), "1");
      else localStorage.removeItem(followUpKey(id));
    } catch { /* private mode: the session still works, it just forgets */ }
  };
  const openFollowUp = (id) => {
    setFollowUpOpen(true);
    rememberFollowUp(id, true);
  };
  const [streamStatus, setStreamStatus] = useState({ state: "idle", message: "" });
  const datasetRef = useRef(dataset);
  const runCompleteRef = useRef(false);
  const esRef = useRef(null);
  const eventCountRef = useRef(0);
  const selectionRef = useRef(0);
  const streamRef = useRef(0);
  const reconnectTimerRef = useRef(null);
  const reconnectStartedRef = useRef(0);
  const activityTimerRef = useRef(null);
  const settledStreamRef = useRef(false);
  const touchStreamRef = useRef(null);
  const seenEventIdsRef = useRef(new Set());
  const sessionsButtonRef = useRef(null);
  const sessionsDialogRef = useRef(null);
  const activeIdRef = useRef(activeId);
  const activeRunIdRef = useRef(activeRunId);
  const stateRef = useRef(state);
  const queryRef = useRef(searchParams.toString());
  const runningRef = useRef(running);
  const mountedRef = useRef(false);
  const uploadGenerationRef = useRef(0);
  const uploadBusyRef = useRef(false);
  const chatBusyRef = useRef(false);
  const runBusyRef = useRef(false);
  // Set when a spec artifact arrives; consumed by the effect that starts it.
  const autoRunSpecRef = useRef(null);
  const sandboxPanelDismissedRef = useRef(false);

  useEffect(() => { datasetRef.current = dataset; }, [dataset]);
  useEffect(() => { activeIdRef.current = activeId; }, [activeId]);
  useEffect(() => { activeRunIdRef.current = activeRunId; }, [activeRunId]);
  useEffect(() => { stateRef.current = state; }, [state]);
  useEffect(() => { queryRef.current = searchParams.toString(); }, [searchParams]);
  useEffect(() => { runningRef.current = running; }, [running]);

  // Live events belong to a run this client just started, and this client can
  // only start real runs. A restored session does not use this fallback: the
  // restore path passes the session's own declared provenance explicitly, so a
  // historical synthetic run keeps its synthetic labelling.
  const provenanceFor = (data = {}, fallbackMode = RUN_MODE) =>
    authoritativeProvenance(data, {
      mode: fallbackMode,
      datasetKind: datasetRef.current?.kind || "unknown",
      source: "session",
    });

  const settleStreamFailure = (message) => {
    closeEvents();
    setTyping(false);
    setLoadingSession(false);
    setRunning(false);
    setStopping(false);
    setStreamStatus({ state: "failed", message, retryable: false });
  };

  const refreshSessions = async () => {
    try {
      setSessions(await listSessions());
    } catch (e) {
      /* server may be down; ignore */
    }
  };

  useEffect(() => {
    refreshSessions();
    const t = setInterval(refreshSessions, 5000);
    return () => clearInterval(t);
  }, []);

  const closeEvents = (preserveReconnect = false) => {
    streamRef.current += 1;
    clearTimeout(reconnectTimerRef.current);
    clearTimeout(activityTimerRef.current);
    reconnectTimerRef.current = null;
    activityTimerRef.current = null;
    if (!preserveReconnect) reconnectStartedRef.current = 0;
    if (!preserveReconnect) settledStreamRef.current = false;
    touchStreamRef.current = null;
    esRef.current?.close();
    esRef.current = null;
  };

  const wireEvents = async (sessionId, expectIdle = false, reconnect = false) => {
    closeEvents(reconnect);
    settledStreamRef.current = expectIdle;
    setStreamStatus({ state: reconnect ? "reconnecting" : "connecting", message: reconnect ? "Refreshing access and reconnecting" : "Connecting to run updates" });
    const stream = streamRef.current;
    const scope = selectionRef.current;
    let es;
    try {
      es = await openEvents(sessionId);
    } catch {
      if (streamRef.current === stream && selectionRef.current === scope) {
        settleStreamFailure("Live updates could not be authenticated.");
      }
      return false;
    }
    if (streamRef.current !== stream || selectionRef.current !== scope) {
      es.close();
      return false;
    }
    const shouldProcess = (event) => {
      if (!event.lastEventId) return true;
      if (seenEventIdsRef.current.has(event.lastEventId)) return false;
      seenEventIdsRef.current.add(event.lastEventId);
      eventCountRef.current = Math.max(
        eventCountRef.current,
        Number(event.lastEventId) + 1
      );
      return true;
    };
    esRef.current = es;
    const isCurrentStream = () => streamRef.current === stream && esRef.current === es;
    const scheduleReconnect = () => {
      const wasSettled = settledStreamRef.current;
      es.close();
      if (esRef.current === es) esRef.current = null;
      streamRef.current += 1;
      clearTimeout(activityTimerRef.current);
      activityTimerRef.current = null;
      // The server closes the response after a settled turn. That end of stream
      // is the expected shape of a finished run, not a fault, so it does not
      // reconnect. The next chat or run opens a fresh stream on demand.
      if (wasSettled) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
        reconnectStartedRef.current = 0;
        setStreamStatus({ state: "complete", message: "Run updates complete" });
        return;
      }
      if (!reconnectStartedRef.current) reconnectStartedRef.current = Date.now();
      const remaining = RECONNECT_TIMEOUT_MS - (Date.now() - reconnectStartedRef.current);
      if (remaining <= 0) {
        setTyping(false);
        setRunning(false);
        setStopping(false);
        setStreamStatus({ state: "failed", message: "Live updates timed out. Reconnect to resume.", retryable: true });
        return;
      }
      const retryGeneration = streamRef.current;
      setStreamStatus({ state: "reconnecting", message: "Connection interrupted, refreshing access" });
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        if (streamRef.current !== retryGeneration || esRef.current) return;
        wireEvents(sessionId, false, true);
      }, Math.min(1000, remaining));
    };
    const markActivity = () => {
      if (!isCurrentStream()) return;
      clearTimeout(activityTimerRef.current);
      activityTimerRef.current = setTimeout(() => {
        if (!isCurrentStream()) return;
        es.close();
        esRef.current = null;
        setTyping(false);
        setRunning(false);
        setStopping(false);
        setStreamStatus({ state: "failed", message: "No run updates were received for five minutes. Reconnect to resume.", retryable: true });
      }, STREAM_ACTIVITY_TIMEOUT_MS);
    };
    touchStreamRef.current = markActivity;
    const markConnected = () => {
      if (!isCurrentStream()) return;
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
      reconnectStartedRef.current = 0;
      if (settledStreamRef.current) {
        clearTimeout(activityTimerRef.current);
        activityTimerRef.current = null;
        setStreamStatus({ state: "complete", message: "Ready for more updates" });
      } else {
        setStreamStatus({ state: "connected", message: "Live updates connected" });
        markActivity();
      }
    };
    es.onopen = markConnected;

    // Dedup runs before any state is touched. A replayed event is not new
    // activity, so it can never reopen a stream this client already settled.
    es.addEventListener("delta", (e) => {
      if (!isCurrentStream()) return;
      if (!shouldProcess(e)) return;
      settledStreamRef.current = false;
      markActivity();
      const data = parseEvent(e);
      if (!data) return;
      const { text = "" } = data;
      setTyping(false);
      setState((s) => {
        if (s.scope !== scope) return s;
        const msgs = [...s.messages];
        const last = msgs[msgs.length - 1];
        if (last && last.role === "assistant" && last.streaming) {
          msgs[msgs.length - 1] = { ...last, text: last.text + text };
        } else {
          msgs.push({ role: "assistant", text, streaming: true });
        }
        return { ...s, messages: msgs, provenance: s.provenance || provenanceFor() };
      });
    });

    es.addEventListener("artifact", (e) => {
      if (!isCurrentStream()) return;
      if (!shouldProcess(e)) return;
      settledStreamRef.current = false;
      markActivity();
      const data = parseEvent(e);
      if (!data) return;
      if (["sandbox_log", "sandbox_file"].includes(data.kind) &&
          !sandboxPanelDismissedRef.current) {
        setSandboxPanelOpen(true);
      }
      const applyArtifact = () => setState((s) => {
        if (s.scope !== scope) return s;
        const artifactProvenance = provenanceFor(data);
        const provenance = hasAuthoritativeProvenance(data)
          ? artifactProvenance
          : (s.provenance || provenanceFor());
        switch (data.kind) {
          case "spec":
            /* A settled spec starts its run. Benchmarking is the product, and a
               spec that sits waiting for a click is a session that produced a
               plan instead of evidence. The confirmation gate that used to live
               here has already been passed: intake asks for confirmation with a
               direction card BEFORE it searches, so by the time a spec exists
               the user has already said yes to this direction. Editing a
               candidate list still re-runs explicitly from the spec card. */
            setTyping(false);
            autoRunSpecRef.current = data.spec;
            return { ...s, spec: data.spec, provenance, specProvenance: artifactProvenance };
          case "direction":
            /* A chat-side card, not a trace row: it is a question put to the
               user, not a record of work done. The turn stops here until they
               answer, so the typing indicator comes down with it. */
            setTyping(false);
            return {
              ...s,
              direction: {
                improved_prompt: data.improved_prompt,
                assumptions: data.assumptions || [],
                unknowns: data.unknowns || [],
              },
              provenance,
            };
          case "trace":
            /* Stamp the turn this call belongs to, so the thread can show the
               agent's work beside the reply it produced instead of pooling
               every call of the session into one block at the foot. The count
               of messages so far IS the turn: the user's message has landed,
               the assistant's has not yet. */
            return {
              ...s,
              trace: [...s.trace, { ...data, turn: s.messages.length, provenance: artifactProvenance }]
                .slice(-MAX_TRACE_EVENTS),
              provenance,
            };
          case "sandbox_log": {
            const logs = { ...s.sandboxLogs };
            const arr = logs[data.sandbox] ? [...logs[data.sandbox]] : [];
            arr.push({ line: data.line, phase: data.phase, provenance: artifactProvenance });
            logs[data.sandbox] = arr.slice(-MAX_LOG_LINES);
            return { ...s, sandboxLogs: logs, provenance };
          }
          case "sandbox_file": {
            const files = { ...s.sandboxFiles };
            const arr = files[data.sandbox] ? [...files[data.sandbox]] : [];
            arr.push({
              path: data.path,
              content: data.content,
              language: data.language,
              revision: data.revision,
              phase: data.phase,
              provenance: artifactProvenance,
            });
            files[data.sandbox] = arr.slice(-20);
            return { ...s, sandboxFiles: files, provenance };
          }
          case "results":
            return {
              ...s,
              results: data.metrics,
              executionMode: data.execution_mode || data.assessment_basis || s.executionMode,
              provenance,
              resultsProvenance: artifactProvenance,
            };
          case "report":
            return {
              ...s,
              report: {
                markdown: data.markdown,
                citations: data.citations,
                provenance: artifactProvenance,
              },
              provenance,
            };
          default:
            return s;
        }
      });
      applyArtifact();
    });

    es.addEventListener("state", (e) => {
      if (!isCurrentStream()) return;
      if (!shouldProcess(e)) return;
      settledStreamRef.current = false;
      markActivity();
      const data = parseEvent(e);
      if (!data) return;
      setState((s) => (s.scope === scope ? {
        ...s,
        phaseState: data,
        provenance: hasAuthoritativeProvenance(data)
          ? provenanceFor(data)
          : (s.provenance || provenanceFor(data)),
      } : s));
      const phase = String(data.phase || "").toUpperCase();
      if (["DONE", "FAILED", "STOPPED"].includes(phase)) setRunning(false);
      refreshSessions();
    });

    es.addEventListener("error", (e) => {
      if (!isCurrentStream()) return;
      if (!shouldProcess(e)) return;
      const data = parseEvent(e);
      if (data?.message) {
        setState((s) => (s.scope === scope ? {
          ...s,
          messages: [...s.messages, { role: "assistant", text: `**Error:** ${data.message}` }],
        } : s));
        setTyping(false);
        setRunning(false);
        setStreamStatus({ state: "failed", message: "The run reported an error", retryable: false });
        return;
      }

      scheduleReconnect();
    });

    es.addEventListener("done", (e) => {
      if (!isCurrentStream()) return;
      if (!shouldProcess(e)) return;
      // The run is over. Close the stream instead of holding an idle
      // connection open: nothing more is coming, and the next chat or run
      // opens a fresh one.
      closeEvents();
      settledStreamRef.current = true;
      setTyping(false);
      setRunning(false);
      setStopping(false);
      setStreamStatus({ state: "complete", message: "Run updates complete" });
      setState((s) => {
        if (s.scope !== scope) return s;
        const msgs = s.messages.map((m) =>
          m.streaming ? { ...m, streaming: false } : m
        );
        return { ...s, messages: msgs };
      });
      refreshSessions();
    });
    return true;
  };

  const onNew = async () => {
    if (uploadBusyRef.current) return;
    selectionRef.current += 1;
    const selection = selectionRef.current;
    closeEvents();
    localStorage.removeItem("proofbench.activeSessionId");
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("session");
      return next;
    });
    setActiveId(null);
    activeIdRef.current = null;
    setActiveRunId(null);
    activeRunIdRef.current = null;
    setState(emptyState(selection));
    sandboxPanelDismissedRef.current = false;
    setSandboxPanelOpen(false);
    setFollowUpOpen(false);
    setRunning(false);
    setStopping(false);
    setTyping(false);
    setLoadingSession(false);
    eventCountRef.current = 0;
    seenEventIdsRef.current = new Set();
    try {
      const { session_id } = await createSession();
      if (selectionRef.current !== selection) return;
      setActiveId(session_id);
      activeIdRef.current = session_id;
      localStorage.setItem("proofbench.activeSessionId", session_id);
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("session", session_id);
        return next;
      });
      refreshSessions();
    } catch (e) {
      if (selectionRef.current !== selection) return;
      // Older development servers may not have the empty-session route yet.
      // The reset remains usable and the first message will create its session.
    }
  };

  const onSelect = async (id) => {
    if (uploadBusyRef.current) return;
    if (id === activeId) return;
    const selection = selectionRef.current + 1;
    selectionRef.current = selection;
    closeEvents();
    setActiveId(id);
    activeIdRef.current = id;
    setActiveRunId(null);
    activeRunIdRef.current = null;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("session", id);
      return next;
    });
    setState(emptyState(selection));
    sandboxPanelDismissedRef.current = false;
    setSandboxPanelOpen(false);
    // Restore whether this session had already been carried on past its run.
    setFollowUpOpen(() => {
      try {
        return localStorage.getItem(followUpKey(id)) === "1";
      } catch {
        return false;
      }
    });
    setRunning(false);
    setLoadingSession(true);
    let knownEventCount = 0;
    let sessionIsRunning = false;
    try {
      const [full, availableDatasets] = await Promise.all([
        getSession(id),
        listDatasets().catch(() => []),
      ]);
      if (selectionRef.current !== selection) return;
      const latestRunId = full.latest_run_id || null;
      let immutableResult = null;
      if (latestRunId) {
        immutableResult = await getResults(latestRunId).catch(() => null);
        if (selectionRef.current !== selection) return;
      }
      setActiveRunId(latestRunId);
      activeRunIdRef.current = latestRunId;
      knownEventCount = Number(full.event_seq) || full.events?.length || 0;
      eventCountRef.current = knownEventCount;
      seenEventIdsRef.current = new Set();
      const resolvedDataset = resolveDatasetSelection(availableDatasets, full.dataset_id);
      const restoredDataset = resolvedDataset.dataset;
      const sessionProvenance = authoritativeProvenance(full, {
        mode: full.mode,
        datasetKind: restoredDataset?.kind || "unknown",
        source: "restored-session",
      });
      const hasArtifacts = Boolean(full.messages?.length || full.events?.length || full.spec || full.results || immutableResult);
      const restored = {
        ...emptyState(selection),
        messages: coalesceRestoredMessages(full.messages),
        spec: full.spec || null,
        results: immutableResult?.metrics || full.results || null,
        executionMode: immutableResult?.execution_mode || immutableResult?.assessment_basis ||
          full.execution_mode || full.assessment_basis || "",
        provenance: hasArtifacts ? sessionProvenance : null,
      };
      if (restored.spec) restored.specProvenance = restored.provenance;
      if (restored.results) {
        restored.resultsProvenance = immutableResult
          ? authoritativeProvenance(immutableResult, sessionProvenance)
          : restored.provenance;
      }
      if (immutableResult?.report_md) restored.report = {
        markdown: immutableResult.report_md,
        citations: immutableResult.citations || [],
        provenance: authoritativeProvenance(immutableResult, sessionProvenance),
      };
      (full.events || []).forEach((item, index) => {
        if (!Array.isArray(item)) return;
        const [sequence, event, data] = item.length >= 3
          ? item
          : [index, item[0], item[1]];
        seenEventIdsRef.current.add(String(sequence));
        if (!data || typeof data !== "object") return;
        if (event === "state") restored.phaseState = data;
        if (event !== "artifact") return;
        const restoredArtifactProvenance = authoritativeProvenance(data, sessionProvenance);
        if (data.kind === "spec") restored.specProvenance = restoredArtifactProvenance;
        if (data.kind === "trace") {
          /* A restored session hands back its messages in one block and its
             events in another, with nothing tying the two orders together — so
             unlike the live path there is no honest per-turn attribution to
             recover. The whole restored trace attaches to the end of the
             conversation rather than being spread across turns on a guess. */
          restored.trace.push({
            ...data,
            turn: restored.messages.length,
            provenance: restoredArtifactProvenance,
          });
          if (restored.trace.length > MAX_TRACE_EVENTS) restored.trace.shift();
        }
        if (data.kind === "sandbox_log") {
          const logs = restored.sandboxLogs[data.sandbox] || [];
          logs.push({ line: data.line, phase: data.phase, provenance: restoredArtifactProvenance });
          if (logs.length > MAX_LOG_LINES) logs.shift();
          restored.sandboxLogs[data.sandbox] = logs;
        }
        if (data.kind === "sandbox_file") {
          const files = restored.sandboxFiles[data.sandbox] || [];
          files.push({
            path: data.path,
            content: data.content,
            language: data.language,
            revision: data.revision,
            phase: data.phase,
            provenance: restoredArtifactProvenance,
          });
          if (files.length > 20) files.shift();
          restored.sandboxFiles[data.sandbox] = files;
        }
        if (data.kind === "results") {
          restored.resultsProvenance = restoredArtifactProvenance;
          restored.executionMode = restored.executionMode ||
            data.execution_mode || data.assessment_basis || "";
        }
        if (data.kind === "report") restored.report = {
          markdown: data.markdown,
          citations: data.citations,
          provenance: restoredArtifactProvenance,
        };
        if (data.kind === "direction") restored.direction = {
          improved_prompt: data.improved_prompt,
          assumptions: data.assumptions || [],
          unknowns: data.unknowns || [],
        };
      });
      /* The gate only ever fires on the opening turn, so a restored session
         that carries a second user message has already answered it. Events and
         messages come back in separate blocks with nothing tying their order
         together, and re-asking a question the user settled is worse than
         dropping a card they can no longer act on. */
      if (restored.direction
          && restored.messages.filter((m) => m.role === "user").length > 1) {
        restored.direction = null;
      }
      setState((s) => (s.scope === selection ? restored : s));
      datasetRef.current = restoredDataset;
      setDataset(restoredDataset);
      setDatasetError(resolvedDataset.error);
      if (restoredDataset?.id) {
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          // Re-assert this restore's own session id: with some router
          // versions the updater's `current` can be a stale snapshot from
          // before this selection, and writing only the dataset param would
          // resurrect the previous session's URL, which the URL-sync effect
          // would then dutifully re-select (observed as "cannot switch
          // sessions while a run is streaming").
          next.set("session", id);
          next.set("dataset", restoredDataset.id);
          return next;
        });
      }
      sessionIsRunning = Boolean(full.is_running);
      setRunning(sessionIsRunning);
      // A session restored mid-run shows its execution too. Waiting for logs to
      // already exist meant reopening a tab during provisioning hid the panel.
      if (sessionIsRunning) setSandboxPanelOpen(true);
      setStreamStatus({ state: sessionIsRunning ? "connecting" : "idle", message: sessionIsRunning ? "Reconnecting to run updates" : "" });
      localStorage.setItem("proofbench.activeSessionId", id);
      setLoadingSession(false);
    } catch {
      if (selectionRef.current !== selection) return;
      closeEvents();
      localStorage.removeItem("proofbench.activeSessionId");
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.delete("session");
        return next;
      });
      setActiveId(null);
      activeIdRef.current = null;
      setActiveRunId(null);
      activeRunIdRef.current = null;
      setLoadingSession(false);
      setState({
        ...emptyState(selection),
        messages: [{ role: "assistant", text: "**Session unavailable:** It may have been deleted or you may not have access." }],
      });
      setStreamStatus({ state: "failed", message: "Session could not be opened.", retryable: false });
      return;
    }
    if (selectionRef.current !== selection) return;
    // A restored session that is not running has nothing left to stream.
    // Opening a connection only for the server to close it made a finished run
    // look like it was reconnecting. onSend and onRun open a fresh stream when
    // there is actually work to follow.
    if (sessionIsRunning) await wireEvents(id);
  };

  const onDelete = async (session) => {
    if (session.is_running || uploadBusyRef.current) return;
    try {
      await deleteSession(session.id);
      rememberFollowUp(session.id, false);
      if (session.id === activeId) {
        selectionRef.current += 1;
        closeEvents();
        localStorage.removeItem("proofbench.activeSessionId");
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          next.delete("session");
          return next;
        });
        setActiveId(null);
        activeIdRef.current = null;
        setActiveRunId(null);
        activeRunIdRef.current = null;
        setState(emptyState(selectionRef.current));
        sandboxPanelDismissedRef.current = false;
        setSandboxPanelOpen(false);
        setFollowUpOpen(false);
        setRunning(false);
        setStopping(false);
        setTyping(false);
        eventCountRef.current = 0;
        seenEventIdsRef.current = new Set();
      }
      await refreshSessions();
    } catch (e) {
      setState((s) => ({
        ...s,
        messages: [...s.messages, { role: "assistant", text: `**Delete failed:** ${e.message}` }],
      }));
    }
  };

  const onSend = async (text) => {
    if (uploadBusyRef.current || datasetError || !acquireOperation(chatBusyRef)) return;
    const selection = selectionRef.current;
    // The server closes the SSE response after each completed turn. Reconnect
    // before sending a follow-up so its reply is not lost.
    try {
      if (activeId && !esRef.current) {
        const opened = await wireEvents(activeId);
        if (!opened) return;
      }
      settledStreamRef.current = false;
      touchStreamRef.current?.();
      setStreamStatus({ state: "connected", message: "Waiting for assistant updates" });
      setTyping(true);
      setState((s) => (s.scope === selection ? {
        ...s,
        // Answered, whichever way they answered it — the card's own buttons
        // come through here too.
        direction: null,
        messages: [...s.messages, { role: "user", text }],
        provenance: s.provenance || authoritativeProvenance({}, {
          mode: RUN_MODE,
          datasetKind: dataset?.kind || "unknown",
          source: "submitted-session",
        }),
      } : s));
      const { session_id } = await postChat(text, activeId, dataset?.dataset_id || dataset?.id);
      if (selectionRef.current !== selection) return;
      // Sending anything after a run settled is the user continuing the
      // session, whether or not they used the follow-up button to get here.
      if (runCompleteRef.current) openFollowUp(session_id);
      if (session_id !== activeId) {
        setActiveId(session_id);
        activeIdRef.current = session_id;
        localStorage.setItem("proofbench.activeSessionId", session_id);
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          next.set("session", session_id);
          return next;
        });
        await wireEvents(session_id);
      }
      refreshSessions();
    } catch (e) {
      if (selectionRef.current !== selection) return;
      settleStreamFailure("The message could not be sent.");
      setState((s) => (s.scope === selection ? {
        ...s,
        messages: [...s.messages, { role: "assistant", text: `**Error:** ${e.message}` }],
      } : s));
    } finally {
      releaseOperation(chatBusyRef);
    }
  };

  // A dataset picked from the library is already on the server: attach it to
  // this view and the URL exactly as a fresh upload would be, minus the upload.
  const onPickExisting = (record) => {
    if (runningRef.current || !record?.id) return;
    const value = {
      id: record.id,
      dataset_id: record.id,
      kind: record.kind,
      title: record.title || "",
      imageCount: record.image_count ?? null,
    };
    datasetRef.current = value;
    setDataset(value);
    setDatasetError("");
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("dataset", record.id);
      return next;
    });
  };

  // The cross on the dataset chip: back to "no dataset yet", including the
  // URL, so the composer offers its attach paths again.
  const onClearDataset = () => {
    if (runningRef.current) return;
    datasetRef.current = null;
    setDataset(null);
    setDatasetError("");
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("dataset");
      return next;
    });
  };

  const onUpload = async (opts) => {
    if (runningRef.current || !acquireOperation(uploadBusyRef)) return;
    const generation = uploadGenerationRef.current + 1;
    uploadGenerationRef.current = generation;
    setUploadingDataset(true);
    const selection = selectionRef.current;
    const sessionId = activeIdRef.current;
    const query = queryRef.current;
    const provenance = stateRef.current.provenance;
    const isCurrent = () => mountedRef.current &&
      uploadGenerationRef.current === generation &&
      selectionRef.current === selection &&
      activeIdRef.current === sessionId &&
      queryRef.current === query &&
      stateRef.current.provenance === provenance &&
      !runningRef.current;
    try {
      const res = await uploadDataset(opts);
      if (!isCurrent()) return;
      const record = {
        id: res.dataset_id,
        label: opts.useSynthetic ? "Sample labelled dataset" : `Uploaded dataset (${opts.images?.length || 0} images)`,
        kind: opts.useSynthetic ? "synthetic" : "upload",
        when: new Date().toISOString(),
        imageCount: opts.useSynthetic ? 15 : (opts.images?.length || null),
      };
      datasetRef.current = record;
      setDataset(record);
      setDatasetError("");
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("dataset", record.id);
        return next;
      });
      setState((s) => (s.scope === selection && s.provenance === provenance ? {
        ...s,
        messages: [
          ...s.messages,
          {
            role: "assistant",
            text: `Dataset ready (id: \`${res.dataset_id}\`).`,
          },
        ],
      } : s));
    } catch (e) {
      if (!isCurrent()) return;
      setState((s) => (s.scope === selection ? {
        ...s,
        messages: [
          ...s.messages,
          { role: "assistant", text: `**Upload failed:** ${e.message}` },
        ],
      } : s));
    } finally {
      if (uploadGenerationRef.current === generation) {
        releaseOperation(uploadBusyRef);
        if (mountedRef.current) setUploadingDataset(false);
      }
    }
  };

  /* Benchmarking is the product: a settled spec runs itself rather than waiting
     to be clicked. Guarded on the same conditions the button is, so a spec that
     arrives while a run is already streaming, or before its dataset resolves,
     waits instead of racing. */
  useEffect(() => {
    const pending = autoRunSpecRef.current;
    if (!pending || !activeId || running || uploadingDataset || datasetError) return;
    if (pending.benchmark_type !== "tool_assessment" && !dataset?.id) return;
    autoRunSpecRef.current = null;
    onRun(pending);
  }, [state.spec, activeId, running, uploadingDataset, datasetError, dataset?.id]);

  const onRun = async (spec) => {
    if (!activeId || runningRef.current || uploadBusyRef.current || datasetError || !acquireOperation(runBusyRef)) return;
    try {
      if (!esRef.current) {
        const opened = await wireEvents(activeId);
        if (!opened) return;
      }
      settledStreamRef.current = false;
      touchStreamRef.current?.();
      setStreamStatus({ state: "connected", message: "Waiting for run updates" });
      setRunning(true);
      runningRef.current = true;
      // A new run replaces the decision, so the thread returns to leading with
      // the fresh ranking rather than the previous conversation.
      setFollowUpOpen(false);
      rememberFollowUp(activeId, false);
      setActiveRunId(null);
      activeRunIdRef.current = null;
      // Execution is the thing the user is waiting on, so the panel opens with
      // the run instead of after the first sandbox happens to log a line: that
      // was several minutes of provisioning and docs work with nothing to look
      // at unless they knew to click. Dismissing it still sticks for the run.
      sandboxPanelDismissedRef.current = false;
      setSandboxPanelOpen(true);
      setState((s) => ({
        ...s,
        sandboxLogs: {},
        sandboxFiles: {},
        results: null,
        resultsProvenance: null,
        report: null,
        provenance: s.provenance || authoritativeProvenance({}, {
          mode: RUN_MODE,
          datasetKind: dataset?.kind || "unknown",
          source: "submitted-run",
        }),
      }));
      const runSpec = buildRunSpec(spec, dataset);
      const started = await startRun(activeId, runSpec);
      if (started?.run_id && activeIdRef.current === activeId) {
        setActiveRunId(started.run_id);
        activeRunIdRef.current = started.run_id;
      }
    } catch (e) {
      settleStreamFailure("The benchmark could not be started.");
      setState((s) => ({
        ...s,
        messages: [
          ...s.messages,
          { role: "assistant", text: `**Run failed:** ${e.message}` },
        ],
      }));
    } finally {
      releaseOperation(runBusyRef);
    }
  };

  const onStop = async () => {
    if (!activeId || stopping) return;
    setStopping(true);
    try {
      await stopRun(activeId);
    } catch (e) {
      setStopping(false);
      setState((s) => ({ ...s, messages: [...s.messages, { role: "assistant", text: `**Stop failed:** ${e.message}` }] }));
    }
  };

  /* The URL is the single source of truth for which session is open.
     Previously a bare /app/benchmark silently reopened whatever was last in
     localStorage, so clicking Benchmark in the nav returned you to an old
     conversation instead of starting one — the opposite of what the nav item
     reads as. Now: a `session` param opens that session, and no param means a
     fresh chat. Nothing is created server-side here; the first message does
     that (see onSend), so visiting the page cannot litter the history with
     empty sessions. */
  useEffect(() => {
    const requested = searchParams.get("session");
    if (requested) {
      if (requested !== activeId) onSelect(requested);
      return;
    }
    if (!activeId) return;
    selectionRef.current += 1;
    closeEvents();
    localStorage.removeItem("proofbench.activeSessionId");
    setActiveId(null);
    activeIdRef.current = null;
    setActiveRunId(null);
    activeRunIdRef.current = null;
    setState(emptyState(selectionRef.current));
    sandboxPanelDismissedRef.current = false;
    setSandboxPanelOpen(false);
    setFollowUpOpen(false);
    setRunning(false);
    setStopping(false);
    setTyping(false);
    setLoadingSession(false);
    eventCountRef.current = 0;
    seenEventIdsRef.current = new Set();
  }, [searchParams, activeId]);

  useEffect(() => {
    const requested = searchParams.get("dataset");
    if (activeId && state.provenance) return;
    if (!requested) {
      if (datasetError) setDatasetError("");
      return;
    }
    if (dataset?.id === requested) return;
    let cancelled = false;
    datasetRef.current = null;
    setDataset(null);
    setDatasetError("Verifying dataset access…");
    listDatasets()
      .then((records) => {
        if (cancelled) return;
        const resolved = resolveDatasetSelection(records, requested);
        datasetRef.current = resolved.dataset;
        setDataset(resolved.dataset);
        setDatasetError(resolved.error);
      })
      .catch(() => {
        if (cancelled) return;
        datasetRef.current = null;
        setDataset(null);
        setDatasetError(`Dataset ${safeVisibleText(requested)} could not be verified. Retry when the dataset service is available.`);
      });
    return () => { cancelled = true; };
  }, [searchParams, dataset?.id, activeId, state.provenance]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      uploadGenerationRef.current += 1;
      closeEvents();
    };
  }, []);

  useEffect(() => {
    if (!sessionsOpen) return undefined;
    const focusTimer = window.setTimeout(() => {
      sessionsDialogRef.current?.querySelector("button:not([disabled])")?.focus();
    }, 0);
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setSessionsOpen(false);
        return;
      }
      if (event.key !== "Tab" || !sessionsDialogRef.current) return;
      const focusable = [...sessionsDialogRef.current.querySelectorAll(
        "a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])"
      )];
      if (focusable.length === 0) {
        event.preventDefault();
        sessionsDialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKeyDown);
      sessionsButtonRef.current?.focus();
    };
  }, [sessionsOpen]);

  const [promptDraft, setPromptDraft] = useState("");
  const activeSession = sessions.find((session) => session.id === activeId);
  const retryEvents = () => activeId && wireEvents(activeId, !running);
  const runPhase = String(state.phaseState?.phase || "").toUpperCase();
  // The completed screen is for reading a decision. A permanently docked
  // composer would spend a fixed band of it on a box nobody is typing in, so
  // the next action is offered as one row and the composer returns on request.
  const runTerminal = ["DONE", "FAILED", "STOPPED"].includes(runPhase);
  const runSettled = runTerminal || (!state.phaseState && !typing);
  // A run that failed or was stopped is just as finished as one that produced a
  // ranking. Requiring results here left exactly that case with no footer and no
  // way forward, which is when the user most needs one.
  const runComplete = runSettled && !running &&
    (runTerminal || Boolean(state.results || state.report));
  const showComposer = !runComplete || followUpOpen;
  useEffect(() => {
    runCompleteRef.current = runComplete;
  }, [runComplete]);
  const provenanceLocked = Boolean(loadingSession || running || state.provenance || state.spec || state.results);
  // Display-only: a restored run reports its own provenance, everything this
  // client can newly write is real. There is no mode the user can change.
  const provenanceMode = state.provenance?.mode || RUN_MODE;
  // During PROVISIONING no sandbox has logged a line yet, but the run has
  // already named its candidates; counting them keeps the Execution button
  // (and its panel) reachable through the slowest, least-informative phase.
  // Only the phases that actually provision count: a tool assessment names
  // every candidate while reading documentation and gives most of them no
  // sandbox, so counting those advertised an execution that never happens.
  const EXECUTION_PHASES = ["PROVISIONING", "BUILDING", "VALIDATING", "RUNNING"];
  const liveCandidateNames =
    running && state.phaseState && EXECUTION_PHASES.includes(
      String(state.phaseState.phase || "").toUpperCase())
      ? Object.keys(state.phaseState.candidates || {})
      : [];
  const sandboxCount = new Set([
    ...Object.keys(state.sandboxLogs),
    ...Object.keys(state.sandboxFiles),
    ...liveCandidateNames,
  ]).size;
  const openSandboxPanel = () => {
    sandboxPanelDismissedRef.current = false;
    setSandboxPanelOpen(true);
  };
  const closeSandboxPanel = () => {
    sandboxPanelDismissedRef.current = true;
    setSandboxPanelOpen(false);
  };

  // No background of its own: the shell's atmosphere sits behind every route,
  // and an opaque fill here would hide it from the glass above.
  return (
    <div className="relative flex h-full min-h-0 w-full overflow-hidden text-[var(--ink)]">
      {sessionsOpen && (
        <div
          className="absolute inset-0 z-30 flex bg-[color-mix(in_oklab,var(--ink)_25%,transparent)]"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSessionsOpen(false);
          }}
        >
          <div
            id="benchmark-sessions-panel"
            ref={sessionsDialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="Benchmark sessions"
            tabIndex={-1}
            className="h-full"
          >
            <Sidebar
              sessions={sessions}
              activeId={activeId}
              onSelect={onSelect}
              onNew={() => { onNew(); setSessionsOpen(false); }}
              onDelete={onDelete}
              onClose={() => setSessionsOpen(false)}
              disabled={uploadingDataset}
            />
          </div>
        </div>
      )}
      <div className="relative flex min-w-0 flex-1 flex-col">
        {/* No page-header band. A chat workspace is the conversation, and a
            full-width bar carrying a static "Benchmark" label spent a fixed
            slice of every screen restating what the selected nav item already
            says. What is actually per-session — the title and run phase — moved
            inline above the thread, where it scrolls with the content; only the
            live controls stay pinned. */}
        <header className="pb-chat-bar pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center gap-3 px-4 sm:px-5">
          {/* The session's own title, hard left. It is the one per-session fact
              worth keeping on screen; "Benchmark" is already the selected nav
              item, so the bar carries no page label. */}
          <h1 className="pb-contain pointer-events-auto min-w-0 flex-1 truncate text-[14px] font-medium text-[var(--ink)]">
            {safeVisibleText(activeSession?.title || (activeId ? `Session ${activeId}` : ""))}
          </h1>
          <div className="pointer-events-auto flex shrink-0 items-center gap-2">
            {/* Live run state travels with the controls now that there is no
                band to hold it: it must stay visible while the thread scrolls. */}
            {runPhase && (
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-medium shadow-[var(--shadow-card)] ${
                  runPhase === "DONE"
                    ? "bg-[var(--ok-tint)] text-[var(--ok)]"
                    : runPhase === "FAILED" || runPhase === "STOPPED"
                      ? "bg-[var(--danger-tint)] text-[var(--danger)]"
                      : "bg-[var(--accent-tint)] text-[var(--accent)]"
                }`}
                aria-label={phaseLabel(safeVisibleText(runPhase))}
                aria-live="polite"
              >
                <StatusIcon tone={phaseTone(runPhase)} size={12} />
                <span className="hidden sm:inline">{phaseLabel(safeVisibleText(runPhase))}</span>
              </span>
            )}
            {streamStatus.state === "failed" && streamStatus.retryable && activeId && (
              <button type="button" onClick={retryEvents} className={BTN_SECONDARY}>
                Reconnect
              </button>
            )}
            {sandboxCount > 0 && (
              <button
                type="button"
                aria-label={`${runComplete ? "View execution" : "Execution"} (${sandboxCount})`}
                aria-expanded={sandboxPanelOpen}
                aria-controls="sandbox-execution-panel"
                onClick={openSandboxPanel}
                className={`${BTN_SECONDARY} shrink-0`}
              >
                <svg className="sm:hidden" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m8 9 3 3-3 3" />
                  <path d="M13 15h3" />
                  <rect x="3" y="4" width="18" height="16" rx="2" />
                </svg>
                <span className="hidden sm:inline">
                  {runComplete ? "View execution" : "Execution"} ({sandboxCount})
                </span>
              </button>
            )}
            <button
              ref={sessionsButtonRef}
              type="button"
              aria-label={`Sessions (${sessions.length})`}
              aria-expanded={sessionsOpen}
              aria-controls="benchmark-sessions-panel"
              onClick={() => setSessionsOpen(true)}
              className={`${BTN_SECONDARY} shrink-0 md:hidden`}
            >
              <svg className="sm:hidden" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M8 7h12" />
                <path d="M8 12h12" />
                <path d="M8 17h12" />
                <circle cx="4" cy="7" r="1" fill="currentColor" stroke="none" />
                <circle cx="4" cy="12" r="1" fill="currentColor" stroke="none" />
                <circle cx="4" cy="17" r="1" fill="currentColor" stroke="none" />
              </svg>
              <span className="hidden sm:inline">Sessions ({sessions.length})</span>
            </button>
            <HeaderActions>
              {/* This is the stable primary action for the workspace. Secondary
                  run controls, including the execution viewer, must never take
                  its place when a run changes state. */}
              <button type="button" onClick={onNew} disabled={uploadingDataset} className={`${BTN_PRIMARY} shrink-0`}>
                New benchmark
              </button>
            </HeaderActions>
          </div>
        </header>
        {datasetError && (
          <div
            className="shrink-0 bg-[var(--danger-tint)] px-4 py-2 text-[13px] text-[var(--danger)] sm:px-8"
            role="alert"
          >
            <span className="mx-auto block w-full max-w-canvas">{datasetError}</span>
          </div>
        )}
        <div
          className={`pb-benchmark-body ${
            sandboxPanelOpen ? "pb-benchmark-body--execution-open" : ""
          } ${!showComposer ? "pb-benchmark-body--completed" : ""}`}
        >
          <div className="pb-benchmark-thread">
            <ChatThread
              statusMessage={streamStatus.state !== "idle" ? streamStatus.message : ""}
              statusFailed={streamStatus.state === "failed"}
              messages={state.messages}
              trace={state.trace}
              sandboxLogs={state.sandboxLogs}
              phaseState={state.phaseState}
              typing={typing}
              spec={state.spec}
              results={state.results}
              report={state.report}
              runId={activeRunId}
              onRun={onRun}
              onStop={onStop}
              running={running}
              stopping={stopping}
              mode={provenanceMode}
              datasetId={dataset?.id}
              datasetLabel={dataset?.title || ""}
              provenance={state.provenance}
              specProvenance={state.specProvenance}
              resultsProvenance={state.resultsProvenance}
              executionMode={state.executionMode}
              interactionDisabled={uploadingDataset || Boolean(datasetError)}
              onPickPrompt={setPromptDraft}
              conversationLive={followUpOpen}
              completedFooter={!showComposer}
            />
            {/* The composer belongs to the thread column, not to the page: the
                execution panel opens beside BOTH, so the conversation narrows
                and the panel runs the full height next to it rather than
                overlapping the thread and stopping short of the input. */}
            {showComposer && state.direction && (
              <DirectionCard direction={state.direction} onSend={onSend} />
            )}
            {showComposer ? (
              <Composer
                onSend={onSend}
                onUpload={onUpload}
                onPickExisting={onPickExisting}
                onClearDataset={onClearDataset}
                dataset={dataset}
                provenanceLocked={provenanceLocked || uploadingDataset}
                disabled={uploadingDataset || Boolean(datasetError)}
                injectText={promptDraft}
              />
            ) : (
              <div
                className="pb-chat-footer pointer-events-none absolute inset-x-0 bottom-0 z-20 px-4 pb-4 pt-10 sm:px-8"
                data-completed-run-bar
              >
                <div className="pointer-events-auto mx-auto flex w-full max-w-[var(--thread-w)] flex-wrap items-center gap-2">
                  <span className="mr-auto text-[12px] text-[var(--ink-2)]">
                    {runPhase === "DONE"
                      ? "This run is finished. The ranking above is the result."
                      : `This run ended as ${runPhase.toLowerCase()}.`}
                  </span>
                  <button type="button" onClick={() => openFollowUp(activeId)} className={BTN_SECONDARY}>
                    Ask a follow-up
                  </button>
                  <button type="button" onClick={onNew} disabled={uploadingDataset} className={BTN_PRIMARY}>
                    Start a new benchmark
                  </button>
                </div>
              </div>
            )}
          </div>
          <SandboxExecutionPanel
            open={sandboxPanelOpen}
            onClose={closeSandboxPanel}
            sandboxLogs={state.sandboxLogs}
            sandboxFiles={state.sandboxFiles}
            phaseState={state.phaseState}
            running={running}
          />
        </div>
      </div>
    </div>
  );
}
