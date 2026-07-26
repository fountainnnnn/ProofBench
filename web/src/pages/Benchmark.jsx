import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Sidebar from "../components/Sidebar.jsx";
import ChatThread from "../components/ChatThread.jsx";
import Composer from "../components/Composer.jsx";
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
  phaseState: null,
  spec: null,
  results: null,
  report: null,
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
  const [dataset, setDataset] = useState(() => searchParams.get("dataset")
    ? null
    : { kind: "synthetic", label: "Sample labelled dataset" });
  const [activeRunId, setActiveRunId] = useState(null);
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [typing, setTyping] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const [uploadingDataset, setUploadingDataset] = useState(false);
  const [datasetError, setDatasetError] = useState(() => searchParams.get("dataset") ? "Verifying dataset access…" : "");
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [followUpOpen, setFollowUpOpen] = useState(false);
  const [streamStatus, setStreamStatus] = useState({ state: "idle", message: "" });
  const datasetRef = useRef(dataset);
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
      const applyArtifact = () => setState((s) => {
        if (s.scope !== scope) return s;
        const artifactProvenance = provenanceFor(data);
        const provenance = hasAuthoritativeProvenance(data)
          ? artifactProvenance
          : (s.provenance || provenanceFor());
        switch (data.kind) {
          case "spec":
            setTyping(false);
            return { ...s, spec: data.spec, provenance, specProvenance: artifactProvenance };
          case "trace":
            return { ...s, trace: [...s.trace, { ...data, provenance: artifactProvenance }].slice(-MAX_TRACE_EVENTS), provenance };
          case "sandbox_log": {
            const logs = { ...s.sandboxLogs };
            const arr = logs[data.sandbox] ? [...logs[data.sandbox]] : [];
            arr.push({ line: data.line, phase: data.phase, provenance: artifactProvenance });
            logs[data.sandbox] = arr.slice(-MAX_LOG_LINES);
            return { ...s, sandboxLogs: logs, provenance };
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
    setFollowUpOpen(false);
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
          restored.trace.push({ ...data, provenance: restoredArtifactProvenance });
          if (restored.trace.length > MAX_TRACE_EVENTS) restored.trace.shift();
        }
        if (data.kind === "sandbox_log") {
          const logs = restored.sandboxLogs[data.sandbox] || [];
          logs.push({ line: data.line, phase: data.phase, provenance: restoredArtifactProvenance });
          if (logs.length > MAX_LOG_LINES) logs.shift();
          restored.sandboxLogs[data.sandbox] = logs;
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
      });
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
        messages: [...s.messages, { role: "user", text }],
        provenance: s.provenance || authoritativeProvenance({}, {
          mode: RUN_MODE,
          datasetKind: dataset?.kind || "unknown",
          source: "submitted-session",
        }),
      } : s));
      const { session_id } = await postChat(text, activeId, dataset?.dataset_id || dataset?.id);
      if (selectionRef.current !== selection) return;
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
      setFollowUpOpen(false);
      setActiveRunId(null);
      activeRunIdRef.current = null;
      setState((s) => ({
        ...s,
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

  useEffect(() => {
    const requested = searchParams.get("session");
    const requestedDataset = searchParams.get("dataset");
    const remembered = localStorage.getItem("proofbench.activeSessionId");
    const target = requested || (!requestedDataset && !activeId && sessions.some((session) => session.id === remembered) ? remembered : null);
    if (target && target !== activeId) onSelect(target);
  }, [searchParams, sessions, activeId, uploadingDataset]);

  useEffect(() => {
    const requested = searchParams.get("dataset");
    if (activeId && state.provenance) return;
    if (!requested) {
      if (datasetError) setDatasetError("");
      if (!dataset) {
        const fallback = { kind: "synthetic", label: "Sample labelled dataset" };
        datasetRef.current = fallback;
        setDataset(fallback);
      }
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
  const runSettled = ["DONE", "FAILED", "STOPPED"].includes(runPhase) ||
    (!state.phaseState && !typing);
  const runComplete = runSettled && !running && Boolean(state.results || state.report);
  const showComposer = !runComplete || followUpOpen;
  const provenanceLocked = Boolean(loadingSession || running || state.provenance || state.spec || state.results);
  // Display-only: a restored run reports its own provenance, everything this
  // client can newly write is real. There is no mode the user can change.
  const provenanceMode = state.provenance?.mode || RUN_MODE;

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
        <header className="pb-page-header shrink-0 px-4 sm:px-8">
          <div className="mx-auto flex w-full max-w-canvas items-center gap-x-3 gap-y-2 pb-3 pt-3.5">
            <div className="min-w-0 flex-1">
              <h1 className="pb-page-title truncate">
                Benchmark
              </h1>
              <p className="pb-contain mt-0.5 truncate text-[13px] text-[var(--ink-2)]">
                {safeVisibleText(activeSession?.title || (activeId ? `Session ${activeId}` : "No active session"))}
              </p>
            </div>
            <HeaderActions>
              <button
                ref={sessionsButtonRef}
                type="button"
                aria-expanded={sessionsOpen}
                aria-controls="benchmark-sessions-panel"
                onClick={() => setSessionsOpen(true)}
                className={`${BTN_SECONDARY} shrink-0 md:hidden`}
              >
                Sessions ({sessions.length})
              </button>
              {/* A settled run puts "Start a new benchmark" in its footer, at
                  the end of what the reader just finished. Two buttons for one
                  action would be the same offer twice on one screen. */}
              {showComposer && (
                <button type="button" onClick={onNew} disabled={uploadingDataset} className={`${BTN_PRIMARY} shrink-0`}>
                  New benchmark
                </button>
              )}
            </HeaderActions>
          </div>
          {(streamStatus.state !== "idle" || runPhase) && (
            <div className="mx-auto -mt-1 flex w-full max-w-canvas flex-wrap items-center gap-x-3 gap-y-1 pb-2.5">
              {runPhase && (
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px] font-medium ${
                    runPhase === "DONE"
                      ? "bg-[var(--ok-tint)] text-[var(--ok)]"
                      : runPhase === "FAILED" || runPhase === "STOPPED"
                        ? "bg-[var(--danger-tint)] text-[var(--danger)]"
                        : "bg-[var(--accent-tint)] text-[var(--accent)]"
                  }`}
                  aria-live="polite"
                >
                  <StatusIcon tone={phaseTone(runPhase)} size={12} />
                  {phaseLabel(safeVisibleText(runPhase))}
                </span>
              )}
              {streamStatus.state !== "idle" && (
                <span
                  className={`text-[12px] ${streamStatus.state === "failed" ? "text-[var(--danger)]" : "text-[var(--ink-3)]"}`}
                  role="status"
                >
                  {streamStatus.message}
                </span>
              )}
              {streamStatus.state === "failed" && streamStatus.retryable && activeId && (
                <button
                  type="button"
                  onClick={retryEvents}
                  className={BTN_SECONDARY}
                >
                  Reconnect
                </button>
              )}
            </div>
          )}
        </header>
        {datasetError && (
          <div
            className="shrink-0 bg-[var(--danger-tint)] px-4 py-2 text-[13px] text-[var(--danger)] sm:px-8"
            role="alert"
          >
            <span className="mx-auto block w-full max-w-canvas">{datasetError}</span>
          </div>
        )}
        <ChatThread
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
          provenance={state.provenance}
          specProvenance={state.specProvenance}
          resultsProvenance={state.resultsProvenance}
          executionMode={state.executionMode}
          interactionDisabled={uploadingDataset || Boolean(datasetError)}
          onPickPrompt={setPromptDraft}
        />
        {showComposer ? (
          <Composer
            onSend={onSend}
            onUpload={onUpload}
            dataset={dataset}
            provenanceLocked={provenanceLocked || uploadingDataset}
            disabled={uploadingDataset || Boolean(datasetError)}
            injectText={promptDraft}
          />
        ) : (
          <div className="shrink-0 px-4 pb-4 pt-3 sm:px-8">
            <div className="mx-auto flex w-full max-w-[840px] flex-wrap items-center gap-2">
              <span className="mr-auto text-[12px] text-[var(--ink-2)]">
                {runPhase === "DONE"
                  ? "This run is finished. The ranking above is the result."
                  : `This run ended as ${runPhase.toLowerCase()}.`}
              </span>
              <button type="button" onClick={() => setFollowUpOpen(true)} className={BTN_SECONDARY}>
                Ask a follow-up
              </button>
              <button type="button" onClick={onNew} disabled={uploadingDataset} className={BTN_PRIMARY}>
                Start a new benchmark
              </button>
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
