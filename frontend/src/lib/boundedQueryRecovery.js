import { useEffect, useRef } from "react";

import { CONNECTIVITY_RECOVERED_EVENT } from "./startupConnection";


// A transient transport failure (ApiNetworkError) always carries transient=true.
// Checked structurally so this hook stays decoupled from the api module and its
// test mocks; HTTP/business errors (status set) and aborts never set it.
function isTransientTransportError(error) {
  return error?.transient === true;
}


/**
 * Bounded, generation-guarded recovery for a TanStack Query.
 *
 * When the supplied query is currently failed with a transient transport error,
 * a confirmed CONNECTIVITY_RECOVERED_EVENT refetches it exactly once per
 * recovery generation. It never converts an HTTP/business error into a retry,
 * never refetches on unrelated URL changes, and settles quietly when the query
 * is not transient-failed — so a failed initial request is marked reconnecting
 * rather than permanently stuck with no future recovery.
 */
export function useBoundedQueryRecovery(query) {
  const handledGenerationRef = useRef(0);
  const refetch = typeof query?.refetch === "function" ? query.refetch : null;
  const isTransient = isTransientTransportError(query?.error);

  useEffect(() => {
    if (!isTransient || !refetch || typeof window === "undefined") {
      return undefined;
    }
    function handleRecovered(event) {
      const generation = Number(event.detail?.generation || 0);
      if (generation <= handledGenerationRef.current) {
        return;
      }
      handledGenerationRef.current = generation;
      void refetch();
    }
    window.addEventListener(CONNECTIVITY_RECOVERED_EVENT, handleRecovered);
    return () => window.removeEventListener(CONNECTIVITY_RECOVERED_EVENT, handleRecovered);
  }, [isTransient, refetch]);
}
