import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell.jsx";
import Landing from "./pages/Landing.jsx";

// Console pages (Benchmark, Runs, Datasets, Settings) are delivered by
// parallel workstreams into src/pages. Resolve whatever exists at build
// time so this router never blocks the shared build; each page module
// default-exports its component.
const pageModules = import.meta.glob("./pages/*.jsx", { eager: true });

function page(name) {
  return pageModules[`./pages/${name}.jsx`]?.default ?? PageUnavailable;
}

function PageUnavailable() {
  return (
    <div className="flex h-full items-center justify-center">
      <p className="text-sm text-text-2">This section is not available yet.</p>
    </div>
  );
}

const Benchmark = page("Benchmark");
const Runs = page("Runs");
const Datasets = page("Datasets");
const Settings = page("Settings");

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/app" element={<Shell />}>
          <Route index element={<Navigate to="/app/benchmark" replace />} />
          <Route path="benchmark" element={<Benchmark />} />
          <Route path="runs" element={<Runs />} />
          <Route path="datasets" element={<Datasets />} />
          <Route path="settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
