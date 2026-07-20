import { useEffect, useRef, useState } from "react";
import Sidebar from "../components/Sidebar.jsx";
import ChatThread from "../components/ChatThread.jsx";
import Composer from "../components/Composer.jsx";
import {
  postChat,
  uploadDataset,
  startRun,
  stopRun,
  openEvents,
  listSessions,
  createSession,
  deleteSession,
  getSession,
} from "../api.js";

const emptyState = (scope) => ({
  scope,
  messages: [],
  trace: [],
  sandboxLogs: {},
  phaseState: null,
  spec: null,
  results: null,
  report: null,
});
export default function Benchmark() {
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [state, setState] = useState(() => emptyState(0));
  const [dataset, setDataset] = useState(null);
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [mode, setMode] = useState("demo");
  const [typing, setTyping] = useState(false);
  const esRef = useRef(null);
  const eventCountRef = useRef(0);
  const selectionRef = useRef(0);
  const streamRef = useRef(0);

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

  const closeEvents = () => {
    streamRef.current += 1;
    esRef.current?.close();
    esRef.current = null;
  };

  const wireEvents = (sessionId, knownEventCount = 0) => {
    closeEvents();
    const stream = streamRef.current;
    const scope = selectionRef.current;
    const es = openEvents(sessionId);
    const seenIds = new Set(
      Array.from({ length: knownEventCount }, (_, index) => String(index))
    );
    const shouldProcess = (event) => {
      if (!event.lastEventId) return true;
      if (seenIds.has(event.lastEventId)) return false;
      seenIds.add(event.lastEventId);
      eventCountRef.current = Math.max(
        eventCountRef.current,
        Number(event.lastEventId) + 1
      );
      return true;
    };
    esRef.current = es;
    const isCurrentStream = () => streamRef.current === stream && esRef.current === es;

    es.addEventListener("delta", (e) => {
      if (!isCurrentStream()) return;
      if (!shouldProcess(e)) return;
      const { text } = JSON.parse(e.data);
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
        return { ...s, messages: msgs };
      });
    });

    es.addEventListener("artifact", (e) => {
      if (!isCurrentStream()) return;
      if (!shouldProcess(e)) return;
      const data = JSON.parse(e.data);
      const applyArtifact = () => setState((s) => {
        if (s.scope !== scope) return s;
        switch (data.kind) {
          case "spec":
            setTyping(false);
            return { ...s, spec: data.spec };
          case "trace":
            return { ...s, trace: [...s.trace, data] };
          case "sandbox_log": {
            const logs = { ...s.sandboxLogs };
            const arr = logs[data.sandbox] ? [...logs[data.sandbox]] : [];
            arr.push({ line: data.line, phase: data.phase });
            logs[data.sandbox] = arr;
            return { ...s, sandboxLogs: logs };
          }
          case "results":
            return { ...s, results: data.metrics };
          case "report":
            return {
              ...s,
              report: { markdown: data.markdown, citations: data.citations },
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
      const data = JSON.parse(e.data);
      setState((s) => (s.scope === scope ? { ...s, phaseState: data } : s));
      refreshSessions();
    });

    es.addEventListener("error", (e) => {
      if (!isCurrentStream()) return;
      if (!shouldProcess(e)) return;
      try {
        const data = JSON.parse(e.data);
        setState((s) => (s.scope === scope ? {
          ...s,
          messages: [...s.messages, { role: "assistant", text: `**Error:** ${data.message}` }],
        } : s));
      } catch {
        /* EventSource connection error, not a server 'error' event */
      }
    });

    es.addEventListener("done", () => {
      if (!isCurrentStream()) return;
      setTyping(false);
      setRunning(false);
      setStopping(false);
      if (esRef.current === es) {
        es.close();
        esRef.current = null;
      }
      setState((s) => {
        if (s.scope !== scope) return s;
        const msgs = s.messages.map((m) =>
          m.streaming ? { ...m, streaming: false } : m
        );
        return { ...s, messages: msgs };
      });
      refreshSessions();
    });
  };

  const onNew = async () => {
    selectionRef.current += 1;
    const selection = selectionRef.current;
    closeEvents();
    localStorage.removeItem("proofbench.activeSessionId");
    setActiveId(null);
    setState(emptyState(selection));
    setDataset(null);
    setRunning(false);
    setStopping(false);
    setMode("demo");
    setTyping(false);
    eventCountRef.current = 0;
    try {
      const { session_id } = await createSession();
      if (selectionRef.current !== selection) return;
      setActiveId(session_id);
      localStorage.setItem("proofbench.activeSessionId", session_id);
      refreshSessions();
    } catch (e) {
      if (selectionRef.current !== selection) return;
      // Older development servers may not have the empty-session route yet.
      // The reset remains usable and the first message will create its session.
    }
  };

  const onSelect = async (id) => {
    if (id === activeId) return;
    const selection = selectionRef.current + 1;
    selectionRef.current = selection;
    closeEvents();
    setActiveId(id);
    setState(emptyState(selection));
    setRunning(false);
    let knownEventCount = 0;
    try {
      const full = await getSession(id);
      if (selectionRef.current !== selection) return;
      knownEventCount = full.events?.length || 0;
      eventCountRef.current = knownEventCount;
      const restored = {
        ...emptyState(selection),
        messages: (full.messages || []).map(({ role, text }) => ({ role, text })),
        spec: full.spec || null,
        results: full.results || null,
      };
      (full.events || []).forEach(([event, data]) => {
        if (event === "state") restored.phaseState = data;
        if (event !== "artifact") return;
        if (data.kind === "trace") restored.trace.push(data);
        if (data.kind === "sandbox_log") {
          const logs = restored.sandboxLogs[data.sandbox] || [];
          logs.push({ line: data.line, phase: data.phase });
          restored.sandboxLogs[data.sandbox] = logs;
        }
        if (data.kind === "report") restored.report = { markdown: data.markdown, citations: data.citations };
      });
      setState((s) => (s.scope === selection ? restored : s));
      setMode(full.mode || "demo");
      setRunning(Boolean(full.is_running));
      localStorage.setItem("proofbench.activeSessionId", id);
    } catch {
      /* ignore */
    }
    if (selectionRef.current !== selection) return;
    wireEvents(id, knownEventCount);
  };

  const onDelete = async (session) => {
    if (session.is_running) return;
    try {
      await deleteSession(session.id);
      if (session.id === activeId) {
        selectionRef.current += 1;
        closeEvents();
        localStorage.removeItem("proofbench.activeSessionId");
        setActiveId(null);
        setState(emptyState(selectionRef.current));
        setRunning(false);
        setStopping(false);
        setTyping(false);
        eventCountRef.current = 0;
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
    const selection = selectionRef.current;
    // The server closes the SSE response after each completed turn. Reconnect
    // before sending a follow-up so its reply is not lost.
    if (activeId && !esRef.current) {
      wireEvents(activeId, eventCountRef.current);
    }
    setTyping(true);
    setState((s) => (s.scope === selection ? {
      ...s,
      messages: [...s.messages, { role: "user", text }],
    } : s));
    try {
      const { session_id } = await postChat(text, activeId, dataset?.dataset_id, mode);
      if (selectionRef.current !== selection) return;
      if (session_id !== activeId) {
        setActiveId(session_id);
        localStorage.setItem("proofbench.activeSessionId", session_id);
        wireEvents(session_id);
      }
      refreshSessions();
    } catch (e) {
      if (selectionRef.current !== selection) return;
      setTyping(false);
      setState((s) => (s.scope === selection ? {
        ...s,
        messages: [...s.messages, { role: "assistant", text: `**Error:** ${e.message}` }],
      } : s));
    }
  };

  const onUpload = async (opts) => {
    try {
      const res = await uploadDataset(opts);
      setDataset(res);
      setState((s) => ({
        ...s,
        messages: [
          ...s.messages,
          {
            role: "assistant",
            text: `Dataset ready at \`${res.path}\` (id: ${res.dataset_id}).`,
          },
        ],
      }));
    } catch (e) {
      setState((s) => ({
        ...s,
        messages: [
          ...s.messages,
          { role: "assistant", text: `**Upload failed:** ${e.message}` },
        ],
      }));
    }
  };

  const onRun = async (spec) => {
    if (!activeId) return;
    setRunning(true);
    setState((s) => ({ ...s, results: null, report: null }));
    try {
      await startRun(activeId, spec, mode);
    } catch (e) {
      setRunning(false);
      setState((s) => ({
        ...s,
        messages: [
          ...s.messages,
          { role: "assistant", text: `**Run failed:** ${e.message}` },
        ],
      }));
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
    const remembered = localStorage.getItem("proofbench.activeSessionId");
    if (!activeId && remembered && sessions.some((session) => session.id === remembered)) onSelect(remembered);
  }, [sessions, activeId]);

  useEffect(() => () => closeEvents(), []);

  return (
    <div className="flex h-full min-h-0 w-full overflow-hidden bg-[var(--bg)] text-[var(--text)]">
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        onSelect={onSelect}
        onNew={onNew}
        onDelete={onDelete}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <ChatThread
          messages={state.messages}
          trace={state.trace}
          sandboxLogs={state.sandboxLogs}
          phaseState={state.phaseState}
          typing={typing}
          spec={state.spec}
          results={state.results}
          report={state.report}
          runId={activeId}
          onRun={onRun}
          onStop={onStop}
          running={running}
          stopping={stopping}
        />
        <Composer
          onSend={onSend}
          onUpload={onUpload}
          dataset={dataset}
          mode={mode}
          onModeChange={setMode}
          disabled={false}
        />
      </main>
    </div>
  );
}
