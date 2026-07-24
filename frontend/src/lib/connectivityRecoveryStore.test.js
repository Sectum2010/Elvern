import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  getConnectivityIncidentAfterFailure,
  getConnectivityIncidentRecoveryGeneration,
  getConnectivityRecoverySnapshot,
  publishConnectivityRecovery,
  registerConnectivityFailure,
  resetConnectivityRecoveryStoreForTests,
  subscribeConnectivityRecovery,
  wasConnectivityIncidentRecovered,
} from "./connectivityRecoveryStore.js";


describe("connectivity recovery store", () => {
  beforeEach(() => {
    resetConnectivityRecoveryStoreForTests();
  });

  test("retains an incident and its recovery when no subscriber is mounted", () => {
    const failure = registerConnectivityFailure();
    const recovery = publishConnectivityRecovery({
      recoveredThroughFailureId: failure.failureId,
    });

    expect(recovery).toMatchObject({ incidentId: failure.incidentId });
    expect(getConnectivityRecoverySnapshot()).toMatchObject({
      active: false,
      latestRecoveredFailureId: failure.failureId,
      latestRecoveredIncidentId: failure.incidentId,
    });
    expect(wasConnectivityIncidentRecovered(failure.incidentId, failure.failureId)).toBe(true);
    expect(getConnectivityIncidentRecoveryGeneration(
      failure.incidentId,
      failure.failureId,
    )).toBeGreaterThan(0);
  });

  test("an old probe cannot close an incident after a newer failure arrives", () => {
    const first = registerConnectivityFailure();
    const second = registerConnectivityFailure();

    expect(publishConnectivityRecovery({
      recoveredThroughFailureId: first.failureId,
    })).toBeNull();
    expect(getConnectivityRecoverySnapshot()).toMatchObject({
      active: true,
      latestFailureId: second.failureId,
    });

    expect(publishConnectivityRecovery({
      recoveredThroughFailureId: second.failureId,
    })).toMatchObject({ recoveredThroughFailureId: second.failureId });
  });

  test("duplicate recovery publication is ignored and subscribers receive one state change", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeConnectivityRecovery(listener);
    const failure = registerConnectivityFailure();
    listener.mockClear();

    expect(publishConnectivityRecovery({
      recoveredThroughFailureId: failure.failureId,
    })).not.toBeNull();
    expect(publishConnectivityRecovery({
      recoveredThroughFailureId: failure.failureId,
    })).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  test("finds an incident that opened after an attach failure watermark", () => {
    const attachWatermark = getConnectivityRecoverySnapshot().latestFailureId;
    const failure = registerConnectivityFailure();

    expect(getConnectivityIncidentAfterFailure(attachWatermark)).toMatchObject({
      incidentId: failure.incidentId,
      firstFailureId: failure.failureId,
      latestFailureId: failure.failureId,
      active: true,
      recovered: false,
    });
    expect(getConnectivityIncidentAfterFailure(failure.failureId)).toBeNull();
  });

  test("an evicted recovered incident keeps a safe global recovery watermark", () => {
    const first = registerConnectivityFailure();
    const firstRecovery = publishConnectivityRecovery({
      recoveredThroughFailureId: first.failureId,
    });
    for (let index = 0; index < 70; index += 1) {
      const failure = registerConnectivityFailure();
      publishConnectivityRecovery({
        recoveredThroughFailureId: failure.failureId,
      });
    }

    expect(getConnectivityIncidentRecoveryGeneration(
      first.incidentId,
      first.failureId,
    )).toBeGreaterThanOrEqual(firstRecovery.generation);
  });

  test("global recovery fallback never marks an unrecovered failure as recovered", () => {
    const recovered = registerConnectivityFailure();
    publishConnectivityRecovery({
      recoveredThroughFailureId: recovered.failureId,
    });
    for (let index = 0; index < 70; index += 1) {
      const failure = registerConnectivityFailure();
      publishConnectivityRecovery({
        recoveredThroughFailureId: failure.failureId,
      });
    }
    const active = registerConnectivityFailure();

    expect(getConnectivityIncidentRecoveryGeneration(
      active.incidentId,
      active.failureId,
    )).toBe(0);
  });
});
