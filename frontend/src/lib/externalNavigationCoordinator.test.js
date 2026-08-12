import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  beginExternalNavigation,
  completeExternalNavigation,
  createExternalNavigationAwareRequestOwner,
  getExternalNavigationSnapshot,
  prepareExternalNavigation,
  resetExternalNavigationCoordinatorForTests,
} from "./externalNavigationCoordinator.js";


describe("external navigation coordinator", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetExternalNavigationCoordinatorForTests();
  });

  afterEach(() => {
    resetExternalNavigationCoordinatorForTests();
    vi.useRealTimers();
  });

  test("binds an opaque handle to identity, provider, and operation", () => {
    const handle = beginExternalNavigation({
      identity: "7:admin",
      provider: "google_drive",
      operationId: "operation-a",
    });

    expect(getExternalNavigationSnapshot()).toMatchObject({
      active: true,
      identity: "7:admin",
      provider: "google_drive",
      operationId: "operation-a",
      phase: "starting",
    });
    expect(completeExternalNavigation({})).toBe(false);
    expect(completeExternalNavigation(handle)).toBe(true);
  });

  test("aborts owned page reads with an expected external-navigation reason", () => {
    const owner = createExternalNavigationAwareRequestOwner({
      identity: "7:admin",
      resource: "cloudLibraries",
    });
    const handle = beginExternalNavigation({
      identity: "7:admin",
      provider: "google_drive",
      operationId: "operation-a",
    });

    prepareExternalNavigation(handle);

    expect(owner.signal.aborted).toBe(true);
    expect(owner.signal.reason).toMatchObject({
      category: "cancellation",
      reason: "expected_external_navigation",
      operationId: "operation-a",
    });
  });

  test("refuses reads for another identity while navigation is suspended", () => {
    const handle = beginExternalNavigation({
      identity: "7:admin",
      provider: "google_drive",
      operationId: "operation-a",
    });
    prepareExternalNavigation(handle);

    const oldIdentityOwner = createExternalNavigationAwareRequestOwner({
      identity: "7:admin",
      resource: "ownTotp",
    });
    const newIdentityOwner = createExternalNavigationAwareRequestOwner({
      identity: "8:admin",
      resource: "ownTotp",
    });

    expect(oldIdentityOwner.signal.aborted).toBe(true);
    expect(newIdentityOwner.signal.aborted).toBe(false);
  });
});
