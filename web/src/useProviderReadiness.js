import { useEffect, useState } from "react";
import { getProviderReadiness } from "./api.js";

/* Provider readiness is a configuration check the server answers without
   contacting any provider, so polling it is free. One hook shared by the
   header chip and the environment rail card. */
export function useProviderReadiness(intervalMs = 60000) {
  const [readiness, setReadiness] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const data = await getProviderReadiness();
        if (alive) {
          setReadiness(data);
          setFailed(false);
        }
      } catch {
        if (alive) setFailed(true);
      }
    };
    load();
    const t = setInterval(load, intervalMs);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [intervalMs]);

  return { readiness, failed };
}
