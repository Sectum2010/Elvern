import { useEffect, useState } from "react";

import { settleAuthViewportBeforeNavigation } from "./iosViewportCoordinator.js";


export async function prepareAuthViewportExit() {
  return settleAuthViewportBeforeNavigation();
}


export function useAuthViewportRedirectReady(active) {
  const [ready, setReady] = useState(!active);

  useEffect(() => {
    let cancelled = false;
    if (!active) {
      setReady(false);
      return () => {
        cancelled = true;
      };
    }
    void prepareAuthViewportExit().finally(() => {
      if (!cancelled) {
        setReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [active]);

  return active && ready;
}
