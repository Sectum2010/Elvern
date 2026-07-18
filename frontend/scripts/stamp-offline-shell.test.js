import { describe, expect, test } from "vitest";

import {
  computeOfflineShellRevision,
  OFFLINE_SHELL_REVISION_PLACEHOLDER,
  PUBLIC_CONNECTIVITY_PROBE_URL_PLACEHOLDER,
  stampPublicConnectivityProbeUrl,
  stampServiceWorkerSource,
} from "./stamp-offline-shell.mjs";


describe("offline shell build revision", () => {
  test("changes whenever offline shell content changes", () => {
    const first = computeOfflineShellRevision("offline shell v1");
    const second = computeOfflineShellRevision("offline shell v2");

    expect(first).toMatch(/^[a-f0-9]{64}$/);
    expect(second).toMatch(/^[a-f0-9]{64}$/);
    expect(second).not.toBe(first);
  });

  test("stamps exactly one revision into the service worker", () => {
    const revision = computeOfflineShellRevision("current offline shell");
    const source = `const revision = "${OFFLINE_SHELL_REVISION_PLACEHOLDER}";`;
    const stamped = stampServiceWorkerSource(source, revision);

    expect(stamped).toContain(revision);
    expect(stamped).not.toContain(OFFLINE_SHELL_REVISION_PLACEHOLDER);
  });

  test("fails closed when the worker placeholder contract drifts", () => {
    const revision = computeOfflineShellRevision("current offline shell");

    expect(() => stampServiceWorkerSource("const revision = 'missing';", revision)).toThrow(
      "Expected exactly one offline shell revision placeholder",
    );
  });

  test("stamps the operator public connectivity probe without changing unrelated content", () => {
    const source = `const probe = "${PUBLIC_CONNECTIVITY_PROBE_URL_PLACEHOLDER}";`;
    expect(stampPublicConnectivityProbeUrl(source, "https://probe.operator.example/health"))
      .toBe('const probe = "https://probe.operator.example/health";');
    expect(stampPublicConnectivityProbeUrl(source, ""))
      .toBe('const probe = "";');
  });
});
