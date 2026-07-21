import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell.jsx";
import Landing from "./pages/Landing.jsx";

const Benchmark = lazy(() => import("./pages/Benchmark.jsx"));
const Runs = lazy(() => import("./pages/Runs.jsx"));
const Datasets = lazy(() => import("./pages/Datasets.jsx"));
const Settings = lazy(() => import("./pages/Settings.jsx"));

function ConsolePage({ children }) {
  return (
    <Suspense fallback={<div className="flex h-full items-center justify-center text-[13px] text-text-2" role="status">Loading section</div>}>
      {children}
    </Suspense>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/app" element={<Shell />}>
          <Route index element={<Navigate to="/app/benchmark" replace />} />
          <Route path="benchmark" element={<ConsolePage><Benchmark /></ConsolePage>} />
          <Route path="runs" element={<ConsolePage><Runs /></ConsolePage>} />
          <Route path="datasets" element={<ConsolePage><Datasets /></ConsolePage>} />
          <Route path="settings" element={<ConsolePage><Settings /></ConsolePage>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
