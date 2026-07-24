import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { apiRequest } from "./api";
import { useBoundedQueryRecovery } from "./boundedQueryRecovery";
import {
  buildLibraryQueryKey,
  buildLibraryShadowV2QueryKey,
  buildLibraryV2QueryKey,
  LIBRARY_QUERY_GC_TIME_MS,
  LIBRARY_QUERY_STALE_TIME_MS,
} from "./libraryQueries";
import {
  adaptLibrarySummaryV2ToLegacyView,
  compareLibraryV1AndV2,
  isLibrarySummaryV2CapabilityFailure,
  isLibrarySummaryV2DebugEnabled,
  LIBRARY_SUMMARY_V2_MODE_OFF,
  LIBRARY_SUMMARY_V2_MODE_ON,
  LIBRARY_SUMMARY_V2_MODE_SHADOW,
  resolveLibrarySummaryV2Mode,
  validateLibrarySummaryV2Payload,
} from "./librarySummaryV2";


function scanPollingInterval(queryState) {
  return queryState.state.data?.scan_in_progress ? 2500 : false;
}


async function requestLibrarySummaryV2(path, signal) {
  const payload = await apiRequest(path, { signal, abortOnPageHide: true });
  return validateLibrarySummaryV2Payload(payload);
}


export function useLibraryViewQuery({
  enabled,
  identity,
  mode: requestedMode,
  searchActive = false,
  v1RequestPath,
  v2RequestPath,
  viewIdentity,
}) {
  const mode = resolveLibrarySummaryV2Mode(requestedMode);
  const comparisonKeysRef = useRef(new Set());
  const [shadowComparison, setShadowComparison] = useState(null);
  const v2Eligible = Boolean(enabled && !searchActive);
  const v1QueryKey = useMemo(() => buildLibraryQueryKey(identity), [identity]);
  const v2QueryKey = useMemo(() => buildLibraryV2QueryKey(identity), [identity]);
  const shadowQueryKey = useMemo(() => buildLibraryShadowV2QueryKey(identity), [identity]);
  const v2Query = useQuery({
    queryKey: v2QueryKey,
    queryFn: ({ signal }) => requestLibrarySummaryV2(v2RequestPath, signal),
    select: adaptLibrarySummaryV2ToLegacyView,
    enabled: v2Eligible && mode === LIBRARY_SUMMARY_V2_MODE_ON,
    staleTime: LIBRARY_QUERY_STALE_TIME_MS,
    gcTime: LIBRARY_QUERY_GC_TIME_MS,
    retry: false,
    refetchInterval: scanPollingInterval,
  });
  const capabilityFallback = mode === LIBRARY_SUMMARY_V2_MODE_ON
    && !searchActive
    && isLibrarySummaryV2CapabilityFailure(v2Query.error);
  const v1Query = useQuery({
    queryKey: v1QueryKey,
    queryFn: ({ signal }) => apiRequest(v1RequestPath, { signal, abortOnPageHide: true }),
    enabled: Boolean(enabled) && (
      mode === LIBRARY_SUMMARY_V2_MODE_OFF
      || mode === LIBRARY_SUMMARY_V2_MODE_SHADOW
      || searchActive
      || capabilityFallback
    ),
    staleTime: LIBRARY_QUERY_STALE_TIME_MS,
    gcTime: LIBRARY_QUERY_GC_TIME_MS,
    retry: false,
    refetchInterval: scanPollingInterval,
  });
  const shadowQuery = useQuery({
    queryKey: shadowQueryKey,
    queryFn: ({ signal }) => requestLibrarySummaryV2(v2RequestPath, signal),
    enabled: v2Eligible && mode === LIBRARY_SUMMARY_V2_MODE_SHADOW,
    staleTime: LIBRARY_QUERY_STALE_TIME_MS,
    gcTime: LIBRARY_QUERY_GC_TIME_MS,
    retry: false,
    refetchInterval: scanPollingInterval,
  });
  useBoundedQueryRecovery(v1Query, {
    enabled: Boolean(v1Query.isError),
    queryKey: v1QueryKey,
  });
  useBoundedQueryRecovery(v2Query, {
    enabled: Boolean(v2Query.isError),
    queryKey: v2QueryKey,
  });

  useEffect(() => {
    if (
      mode !== LIBRARY_SUMMARY_V2_MODE_SHADOW
      || !v1Query.data
      || !shadowQuery.data
    ) {
      return;
    }
    const comparisonKey = JSON.stringify([
      identity,
      v1Query.dataUpdatedAt,
      shadowQuery.data.revision,
    ]);
    if (comparisonKeysRef.current.has(comparisonKey)) {
      return;
    }
    comparisonKeysRef.current.add(comparisonKey);
    const result = compareLibraryV1AndV2(v1Query.data, shadowQuery.data, { viewIdentity });
    setShadowComparison(result);
    if (import.meta.env.MODE === "test" && !result.matches) {
      throw new Error(`Library summary v2 shadow parity failed (${result.mismatchCount} mismatch(es)).`);
    }
    if (isLibrarySummaryV2DebugEnabled()) {
      console.info("[library-summary-v2-shadow]", result);
    }
  }, [identity, mode, shadowQuery.data, v1Query.data, v1Query.dataUpdatedAt, viewIdentity]);

  const useV2 = mode === LIBRARY_SUMMARY_V2_MODE_ON
    && !searchActive
    && !capabilityFallback;
  const activeQuery = useV2 ? v2Query : v1Query;
  const activeQueryKey = useV2 ? v2QueryKey : v1QueryKey;
  return useMemo(() => ({
    ...activeQuery,
    activeQueryKey,
    activeVersion: useV2 ? "v2" : "v1",
    capabilityFallback,
    mode,
    shadowComparison,
    shadowQuery,
    v1Query,
    v2Query,
  }), [
    activeQuery,
    activeQueryKey,
    capabilityFallback,
    mode,
    shadowComparison,
    shadowQuery,
    useV2,
    v1Query,
    v2Query,
  ]);
}
