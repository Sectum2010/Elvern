import { describe, expect, test } from "vitest";

import {
  CONNECTION_RUNTIME_CONTRACT,
  createConnectivityRuntime,
  createOfflineDocumentStateMachine,
  deriveFastOopsCandidate,
  getRecoveryDecision,
  matchesFastOopsCandidates,
} from "./connectivityRuntimeCore.js";


describe("offline document deadline state machine", () => {
  test("latches Oops at the original document deadline and never returns to animation", () => {
    let now = 10_000;
    const machine = createOfflineDocumentStateMachine({
      documentStartedAt: now,
      now: () => now,
    });

    now += CONNECTION_RUNTIME_CONTRACT.offlineDocumentOopsDelayMs - 1;
    machine.advanceDeadline();
    expect(machine.getSnapshot()).toMatchObject({
      state: "connecting",
      visibleState: "connecting",
      oopsLatched: false,
    });

    now += 1;
    machine.advanceDeadline();
    expect(machine.getSnapshot()).toMatchObject({
      state: "oops_latched",
      visibleState: "oops_latched",
      oopsLatched: true,
    });

    now += CONNECTION_RUNTIME_CONTRACT.offlineDocumentOopsDelayMs;
    machine.advanceDeadline();
    expect(machine.getSnapshot().visibleState).toBe("oops_latched");
  });

  test("failed Retry preserves the latch and the immutable deadline", () => {
    let now = 0;
    const machine = createOfflineDocumentStateMachine({
      documentStartedAt: now,
      now: () => now,
    });
    const originalDeadline = machine.getSnapshot().oopsDeadlineAt;

    now = originalDeadline;
    machine.advanceDeadline();
    expect(machine.beginRecovery()).toBe(true);
    expect(machine.getSnapshot()).toMatchObject({
      state: "recovering",
      visibleState: "oops_latched",
    });

    machine.finishRecovery(false);
    expect(machine.getSnapshot()).toMatchObject({
      state: "oops_latched",
      visibleState: "oops_latched",
      oopsDeadlineAt: originalDeadline,
    });
  });

  test("successful recovery is immediate and concurrent recovery is rejected", () => {
    const machine = createOfflineDocumentStateMachine({ documentStartedAt: 5, now: () => 10 });

    expect(machine.beginRecovery()).toBe(true);
    expect(machine.beginRecovery()).toBe(false);
    machine.finishRecovery(true);

    expect(machine.getSnapshot()).toMatchObject({
      state: "recovered",
      visibleState: "recovered",
      recovered: true,
    });
  });

  test("a new machine models a manual reload with a new deadline", () => {
    const first = createOfflineDocumentStateMachine({ documentStartedAt: 100, now: () => 100 });
    const second = createOfflineDocumentStateMachine({ documentStartedAt: 50_000, now: () => 50_000 });

    expect(second.getSnapshot().state).toBe("connecting");
    expect(second.getSnapshot().oopsDeadlineAt).not.toBe(first.getSnapshot().oopsDeadlineAt);
  });

  test("a conclusive Oops latches once without returning to connecting", () => {
    const machine = createOfflineDocumentStateMachine({ documentStartedAt: 100, now: () => 200 });

    machine.latchOops({
      classification: CONNECTION_RUNTIME_CONTRACT.classifications.frontendOrVpnUnreachable,
      evidenceReason: CONNECTION_RUNTIME_CONTRACT.fastOopsReasons.frontendUnreachable,
      confidence: "confirmed",
    });
    machine.latchOops({
      classification: CONNECTION_RUNTIME_CONTRACT.classifications.backendUnreachable,
      evidenceReason: CONNECTION_RUNTIME_CONTRACT.fastOopsReasons.backendUnreachable,
    });

    expect(machine.getSnapshot()).toMatchObject({
      visibleState: "oops_latched",
      oopsClassification: CONNECTION_RUNTIME_CONTRACT.classifications.frontendOrVpnUnreachable,
      oopsEvidenceReason: CONNECTION_RUNTIME_CONTRACT.fastOopsReasons.frontendUnreachable,
      oopsConfidence: "confirmed",
    });
  });

  test("same-host frontend and backend health cannot recover an offline document", () => {
    let now = 0;
    const machine = createOfflineDocumentStateMachine({ documentStartedAt: 0, now: () => now });
    const runtime = createConnectivityRuntime();

    expect(runtime.isVerifiedRecoveryEvidence({
      internetState: "offline",
      frontendHealthy: true,
      backendHealthy: true,
      appShellHealthy: true,
    })).toBe(false);

    now = 60_000;
    machine.advanceDeadline();
    now = 120_000;
    expect(machine.advanceDeadline()).toMatchObject({
      state: "oops_latched",
      visibleState: "oops_latched",
      oopsLatched: true,
    });
  });
});


describe("Fast Oops evidence contract", () => {
  const health = CONNECTION_RUNTIME_CONTRACT.healthEvidenceReasons;
  const publicReasons = CONNECTION_RUNTIME_CONTRACT.publicEvidenceReasons;

  test("recognizes hard frontend and backend evidence but not timeouts or maintenance", () => {
    expect(deriveFastOopsCandidate({
      publicEvidence: { internetState: "online", publicEvidenceReason: publicReasons.endpointSuccess },
      frontendHealth: { reason: health.networkError },
    })).toMatchObject({ evidenceReason: "conclusive_frontend_unreachable" });
    expect(deriveFastOopsCandidate({
      frontendHealth: { reason: health.httpSuccess },
      backendHealth: { reason: health.httpUnhealthy, status: 503 },
    })).toMatchObject({ evidenceReason: "conclusive_backend_unreachable" });
    expect(deriveFastOopsCandidate({
      publicEvidence: { internetState: "online", publicEvidenceReason: publicReasons.endpointSuccess },
      frontendHealth: { reason: health.timeout },
    })).toBeNull();
    expect(deriveFastOopsCandidate({
      frontendHealth: { reason: health.httpSuccess },
      backendHealth: { reason: health.httpUnhealthy, status: 503, maintenance: true },
    })).toBeNull();
  });

  test("requires the same candidate in both confirmation rounds", () => {
    expect(matchesFastOopsCandidates(
      { classification: "vpn", evidenceReason: "network" },
      { classification: "vpn", evidenceReason: "network" },
    )).toBe(true);
    expect(matchesFastOopsCandidates(
      { classification: "vpn", evidenceReason: "network" },
      { classification: "server", evidenceReason: "http" },
    )).toBe(false);
  });
});


describe("offline recovery evidence policy", () => {
  const healthyServices = {
    frontendHealthy: true,
    backendHealthy: true,
    appShellHealthy: true,
  };

  test("automatic recovery still requires public endpoint success", () => {
    expect(getRecoveryDecision({
      trigger: "automatic",
      internetState: "unknown",
      publicEvidenceReason: "probe_failure_unverified",
      ...healthyServices,
    })).toEqual({ accepted: false, recoveryMode: null });
  });

  test.each([
    ["probe_failure_unverified", "unknown"],
    ["probe_failure_trusted", "offline"],
    ["probes_disabled", "unknown"],
  ])("manual Retry permits service-only recovery for %s", (publicEvidenceReason, internetState) => {
    expect(getRecoveryDecision({
      trigger: "manual_retry",
      internetState,
      publicEvidenceReason,
      ...healthyServices,
    })).toEqual({ accepted: true, recoveryMode: "manual_service_only" });
  });

  test("manual Retry cannot bypass an explicit browser offline signal", () => {
    expect(getRecoveryDecision({
      trigger: "manual_retry",
      internetState: "offline",
      publicEvidenceReason: "browser_explicit_offline",
      ...healthyServices,
    })).toEqual({ accepted: false, recoveryMode: null });
  });

  test("manual Retry still requires every Elvern service layer", () => {
    expect(getRecoveryDecision({
      trigger: "manual_retry",
      internetState: "unknown",
      publicEvidenceReason: "probes_disabled",
      frontendHealthy: true,
      backendHealthy: true,
      appShellHealthy: false,
    })).toEqual({ accepted: false, recoveryMode: null });
  });

  test("public endpoint success retains verified-public recovery", () => {
    expect(getRecoveryDecision({
      trigger: "visibility_return",
      internetState: "online",
      publicEvidenceReason: "endpoint_success",
      ...healthyServices,
    })).toEqual({ accepted: true, recoveryMode: "verified_public" });
  });
});
