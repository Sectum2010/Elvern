import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  publishConnectivityRecovery,
  registerConnectivityFailure,
  resetConnectivityRecoveryStoreForTests,
} from "./connectivityRecoveryStore.js";
import {
  requestBoundedQueryRecovery,
  resetBoundedQueryRecoveryForTests,
} from "./boundedQueryRecovery.js";


describe("bounded query recovery", () => {
  beforeEach(() => {
    resetConnectivityRecoveryStoreForTests();
    resetBoundedQueryRecoveryForTests();
  });

  test("claims one refetch per exact query hash and recovery generation", () => {
    const failure = registerConnectivityFailure();
    publishConnectivityRecovery({ recoveredThroughFailureId: failure.failureId });
    const error = {
      transient: true,
      failureId: failure.failureId,
      incidentId: failure.incidentId,
    };
    const refetch = vi.fn();
    const queryKey = ["library", "v1", { userId: "7", category: "movies" }];

    expect(requestBoundedQueryRecovery({ error, queryKey, refetch })).toBe(true);
    expect(requestBoundedQueryRecovery({ error, queryKey, refetch })).toBe(false);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  test("different protected identities do not share a recovery claim", () => {
    const failure = registerConnectivityFailure();
    publishConnectivityRecovery({ recoveredThroughFailureId: failure.failureId });
    const error = {
      transient: true,
      failureId: failure.failureId,
      incidentId: failure.incidentId,
    };
    const refetchA = vi.fn();
    const refetchB = vi.fn();

    expect(requestBoundedQueryRecovery({
      error,
      queryKey: ["user-settings", "v1", { userId: "7", role: "standard_user" }],
      refetch: refetchA,
    })).toBe(true);
    expect(requestBoundedQueryRecovery({
      error,
      queryKey: ["user-settings", "v1", { userId: "8", role: "standard_user" }],
      refetch: refetchB,
    })).toBe(true);
    expect(refetchA).toHaveBeenCalledTimes(1);
    expect(refetchB).toHaveBeenCalledTimes(1);
  });

  test("HTTP and malformed protocol errors are never recovered", () => {
    const failure = registerConnectivityFailure();
    publishConnectivityRecovery({ recoveredThroughFailureId: failure.failureId });
    const refetch = vi.fn();

    expect(requestBoundedQueryRecovery({
      error: { transient: false, failureId: failure.failureId },
      queryKey: ["library", "v1", { userId: "7" }],
      refetch,
    })).toBe(false);
    expect(refetch).not.toHaveBeenCalled();
  });
});
