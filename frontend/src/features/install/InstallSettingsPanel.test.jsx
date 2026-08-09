import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { ApiNetworkError, apiRequest } from "../../lib/api";
import { PAGE_RESUME_EVENT } from "../../lib/pageResume";
import {
  publishConnectivityRecovery,
  registerConnectivityFailure,
  resetConnectivityRecoveryStoreForTests,
} from "../../lib/connectivityRecoveryStore";
import { InstallSettingsPanel } from "./InstallSettingsPanel.jsx";


const platformState = vi.hoisted(() => ({ value: "mac" }));

vi.mock("../../lib/api", async (importOriginal) => ({
  ...(await importOriginal()),
  apiRequest: vi.fn(),
}));
vi.mock("../../lib/device", () => ({ getOrCreateDeviceId: () => "device-test" }));
vi.mock("../../lib/platformDetection", async (importOriginal) => ({
  ...(await importOriginal()),
  detectClientPlatform: () => platformState.value,
}));

function release(overrides = {}) {
  return {
    id: 1,
    channel: "stable",
    runtime_id: "macos-dual-arch",
    platform: "mac",
    package_target: "macos-dual-arch",
    version: "0.9.0",
    filename: "elvern-vlc-opener-0.9.0-macos-dual-arch.zip",
    package_root: "Elvern VLC Opener Installer",
    installer_entrypoint: "Install-ElvernVlcOpener.command",
    size_bytes: 1234,
    sha256: "a".repeat(64),
    installer_manifest_sha256: "b".repeat(64),
    installer_tree_manifest_path: ".elvern/tree-manifest.tsv",
    installer_tree_manifest_sha256: "c".repeat(64),
    package_binding: "compatible",
    published_at: "2026-07-22T00:00:00Z",
    download_url: "/api/desktop-helper/releases/1/download",
    deployment_mode: "self_contained",
    external_runtime_required: false,
    runtime_family: "10.0",
    supported_runtime_ids: ["osx-arm64", "osx-x64"],
    minimum_os_version: "14.0",
    recommended: true,
    ...overrides,
  };
}

function status(overrides = {}) {
  return {
    device_id: "device-test",
    platform: "mac",
    helper_required: true,
    state: "unknown",
    same_host: false,
    same_host_detection_source: "platform_not_linux",
    vlc_detection_state: "detection_unavailable",
    runtime_included: true,
    latest_releases: [release()],
    notes: [],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  resetConnectivityRecoveryStoreForTests();
  platformState.value = "mac";
  apiRequest.mockResolvedValue(status());
});

describe("desktop helper install page", () => {
  test("macOS shows one self-contained package and no removed setup card", async () => {
    render(<InstallSettingsPanel />);

    expect(await screen.findByRole("link", { name: "Download for macOS" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Download for macOS/i })).toHaveLength(1);
    expect(screen.queryByText("Desktop helper setup")).not.toBeInTheDocument();
    expect(screen.queryByText(/macOS Apple Silicon/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/macOS Intel/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Includes Apple Silicon and Intel versions/i)).toBeInTheDocument();
    expect(screen.getByText(/Runtime included/i)).toBeInTheDocument();
    expect(screen.queryByText(/\.NET 8 Required/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/More options/i)).not.toBeInTheDocument();
  });

  test("third-party VLC opens in a new tab while the Helper ZIP remains a same-page download", async () => {
    render(<InstallSettingsPanel />);

    const helperDownload = await screen.findByRole("link", { name: "Download for macOS" });
    const vlcDownload = screen.getByRole("link", { name: "Download VLC" });

    expect(helperDownload).not.toHaveAttribute("target");
    expect(helperDownload).toHaveAttribute(
      "href",
      "/api/desktop-helper/releases/1/download",
    );
    expect(vlcDownload).toHaveAttribute("target", "_blank");
    expect(vlcDownload).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(vlcDownload).toHaveAttribute("href", expect.stringMatching(/^https:\/\//));
  });

  test.each([
    ["iphone", "App Store"],
    ["ipad", "App Store"],
    ["android", "Google Play"],
  ])("%s third-party store links open independently from Elvern", (platform, linkName) => {
    platformState.value = platform;
    render(<InstallSettingsPanel />);

    const storeLinks = screen.getAllByRole("link", { name: linkName });
    expect(storeLinks.length).toBeGreaterThan(0);
    storeLinks.forEach((storeLink) => {
      expect(storeLink).toHaveAttribute("target", "_blank");
      expect(storeLink).toHaveAttribute("rel", expect.stringContaining("noopener"));
      expect(storeLink).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
      expect(storeLink).toHaveAttribute("href", expect.stringMatching(/^https:\/\//));
    });
    expect(apiRequest).not.toHaveBeenCalled();
  });

  test("remote Linux shows one universal helper while same-host Linux hides it", async () => {
    platformState.value = "linux";
    apiRequest.mockResolvedValueOnce(status({
      platform: "linux",
      same_host: false,
      latest_releases: [release({
        id: 2,
        platform: "linux",
        runtime_id: "linux-universal",
        package_target: "linux-universal",
        filename: "elvern-vlc-opener-0.9.0-linux-universal.zip",
        package_root: "Elvern VLC Opener Linux Installer",
        installer_entrypoint: "Install-ElvernVlcOpener.sh",
        supported_runtime_ids: ["linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64"],
      })],
    }));
    const rendered = render(<InstallSettingsPanel />);

    expect(await screen.findByRole("link", { name: "Download for Linux" })).toBeInTheDocument();
    expect(screen.getByText(/x64 and ARM64.*glibc and musl/i)).toBeInTheDocument();

    rendered.unmount();
    apiRequest.mockResolvedValueOnce(status({
      platform: "linux",
      helper_required: false,
      state: "helper_not_required",
      same_host: true,
      latest_releases: [],
    }));
    render(<InstallSettingsPanel />);

    expect(await screen.findByText("Not required on this Elvern host")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Download for Linux" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check VLC on this host" })).toBeInTheDocument();
  });

  test("verification feedback lives in the helper action card as an aria-live status", async () => {
    apiRequest
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce({
        status: status({
          state: "up_to_date",
          vlc_detection_state: "installed",
          vlc_detection_checked_at: "2026-07-22T00:00:00Z",
        }),
      });
    render(<InstallSettingsPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "Test helper" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/called back to Elvern/i);
    });
  });

  test("unknown platform is reported instead of falling back to Linux", () => {
    platformState.value = "unknown";
    render(<InstallSettingsPanel />);

    expect(screen.getByText("Platform could not be detected.")).toBeInTheDocument();
    expect(apiRequest).not.toHaveBeenCalled();
    expect(screen.queryByText(/Detected platform: Linux/i)).not.toBeInTheDocument();
  });

  test("legacy per-RID packages keep one main action and place alternatives under More options", async () => {
    apiRequest.mockResolvedValueOnce(status({
      runtime_included: false,
      latest_releases: [
        release({
          id: 50,
          runtime_id: "osx-arm64",
          package_target: "osx-arm64",
          deployment_mode: "framework_dependent",
          external_runtime_required: true,
          supported_runtime_ids: ["osx-arm64"],
        }),
        release({
          id: 51,
          runtime_id: "osx-x64",
          package_target: "osx-x64",
          deployment_mode: "framework_dependent",
          external_runtime_required: true,
          supported_runtime_ids: ["osx-x64"],
          recommended: false,
        }),
      ],
    }));
    render(<InstallSettingsPanel />);

    expect(await screen.findByRole("link", { name: "Download for macOS" })).toBeInTheDocument();
    expect(screen.getByText("More options...")).toBeInTheDocument();
    expect(screen.queryByText(/Includes Apple Silicon and Intel versions/i)).not.toBeInTheDocument();
  });

  test("returning to the page refreshes status once without continuous polling", async () => {
    render(<InstallSettingsPanel />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(1));

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(2));

    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("an idle Playback & Apps panel performs only its initial status request for 60 seconds", async () => {
    vi.useFakeTimers();
    try {
      render(<InstallSettingsPanel presentation="meridian" />);
      await vi.advanceTimersByTimeAsync(0);
      expect(apiRequest).toHaveBeenCalledTimes(1);

      await vi.advanceTimersByTimeAsync(60_000);
      expect(apiRequest).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  test("Meridian Diagnostics keeps the exact eight-row contract and custom play icon", async () => {
    const rendered = render(<InstallSettingsPanel presentation="meridian" />);
    await screen.findByText("Helper not verified");

    const details = screen.getByText("Diagnostics").closest("details");
    fireEvent.click(within(details).getByText("Diagnostics"));
    expect(within(details).getAllByRole("term").map((node) => node.textContent)).toEqual([
      "Package version",
      "Last seen helper version",
      "Last callback",
      "Reported architecture",
      "Package target",
      "Runtime",
      "Package binding",
      "Device ID",
    ]);
    expect(rendered.container.querySelector(".meridian-icon-tile--play path"))
      .toHaveAttribute("d", "M10 8.6v6.8L15.5 12z");
    expect(within(details).getByText("Diagnostics").querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  test("Meridian same-host Linux includes the grounded playback explanation", async () => {
    platformState.value = "linux";
    apiRequest.mockResolvedValueOnce(status({
      platform: "linux",
      helper_required: false,
      state: "helper_not_required",
      same_host: true,
      latest_releases: [],
    }));

    render(<InstallSettingsPanel presentation="meridian" />);

    expect(await screen.findByText(/Linux same-host playback does not require the desktop helper/)).toBeInTheDocument();
  });

  test("Diagnostics remains open when a passive page-resume status refresh completes", async () => {
    apiRequest
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(status({ state: "up_to_date" }));
    render(<InstallSettingsPanel presentation="meridian" />);
    const details = (await screen.findByText("Diagnostics")).closest("details");
    fireEvent.click(within(details).getByText("Diagnostics"));
    expect(details).toHaveAttribute("open");

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));
    await screen.findByText("Ready");
    expect(details).toHaveAttribute("open");
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("a successful active status request absorbs a queued resume without overlap", async () => {
    let resolveInitial;
    apiRequest.mockImplementationOnce(() => new Promise((resolve) => {
      resolveInitial = resolve;
    }));
    render(<InstallSettingsPanel />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(1));

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));
    expect(apiRequest).toHaveBeenCalledTimes(1);
    resolveInitial(status({ state: "up_to_date" }));

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(apiRequest).toHaveBeenCalledTimes(1);
  });

  test("a failed active status request drains one queued recovery refresh", async () => {
    const failure = registerConnectivityFailure();
    let rejectInitial;
    apiRequest
      .mockImplementationOnce(() => new Promise((_resolve, reject) => {
        rejectInitial = reject;
      }))
      .mockResolvedValueOnce(status({ state: "up_to_date" }));
    render(<InstallSettingsPanel />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(1));

    publishConnectivityRecovery({
      generation: 8,
      recoveredThroughFailureId: failure.failureId,
    });
    rejectInitial(new ApiNetworkError(undefined, failure));

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("a failed active status request drains one queued passive resume", async () => {
    let rejectInitial;
    apiRequest
      .mockImplementationOnce(() => new Promise((_resolve, reject) => {
        rejectInitial = reject;
      }))
      .mockResolvedValueOnce(status({ state: "up_to_date" }));
    render(<InstallSettingsPanel />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(1));

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));
    expect(apiRequest).toHaveBeenCalledTimes(1);
    rejectInitial(new Error("status failed"));

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("a Firefox transport error is shown with stable copy, not the raw browser message", async () => {
    const failure = registerConnectivityFailure();
    apiRequest.mockRejectedValueOnce(new ApiNetworkError(undefined, {
      cause: new TypeError("NetworkError when attempting to fetch resource"),
      ...failure,
    }));
    render(<InstallSettingsPanel />);

    expect(await screen.findByText("Elvern could not load Helper status.")).toBeInTheDocument();
    expect(screen.queryByText(/NetworkError when attempting/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  test("confirmed recovery retries a transient status failure once per generation", async () => {
    const failure = registerConnectivityFailure();
    apiRequest
      .mockRejectedValueOnce(new ApiNetworkError(undefined, failure))
      .mockResolvedValueOnce(status({ state: "up_to_date" }));
    render(<InstallSettingsPanel />);
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();

    publishConnectivityRecovery({
      generation: 9,
      recoveredThroughFailureId: failure.failureId,
    });
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(apiRequest).toHaveBeenCalledTimes(2);

    publishConnectivityRecovery({
      generation: 9,
      recoveredThroughFailureId: failure.failureId,
    });
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(apiRequest).toHaveBeenCalledTimes(2);
  });

  test("resume and recovery requests coalesce behind an active helper verification", async () => {
    const failure = registerConnectivityFailure();
    let resolveVerify;
    apiRequest
      .mockResolvedValueOnce(status())
      .mockRejectedValueOnce(new ApiNetworkError(undefined, failure))
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveVerify = resolve;
      }))
      .mockResolvedValueOnce(status({ state: "up_to_date" }));
    render(<InstallSettingsPanel />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(1));

    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Test helper" }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(3));

    publishConnectivityRecovery({
      generation: 11,
      recoveredThroughFailureId: failure.failureId,
    });
    fireEvent(window, new CustomEvent(PAGE_RESUME_EVENT));
    resolveVerify({ status: status({ state: "up_to_date" }) });

    await waitFor(() => expect(apiRequest).toHaveBeenCalledTimes(4));
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(apiRequest).toHaveBeenCalledTimes(4);
  });

  test("pagehide immediately cancels helper verification polling", async () => {
    vi.useFakeTimers();
    try {
      apiRequest.mockImplementation((path) => {
        if (path.startsWith("/api/desktop-helper/status")) {
          return Promise.resolve(status({ vlc_detection_state: "detection_unavailable" }));
        }
        if (path === "/api/desktop-helper/verify") {
          return Promise.resolve({ protocol_url: "elvern-vlc-opener://verify" });
        }
        return Promise.reject(new Error(`Unexpected request: ${path}`));
      });
      render(<InstallSettingsPanel />);
      await vi.advanceTimersByTimeAsync(0);

      fireEvent.click(screen.getByRole("button", { name: "Test helper" }));
      await vi.advanceTimersByTimeAsync(0);
      const callsBeforePageHide = apiRequest.mock.calls.length;

      fireEvent(window, new Event("pagehide"));
      await vi.advanceTimersByTimeAsync(3000);

      expect(apiRequest.mock.calls.length).toBe(callsBeforePageHide);
      expect(screen.getByRole("button", { name: "Test helper" })).not.toBeDisabled();
    } finally {
      vi.useRealTimers();
    }
  });

  test("helper verification polling stops and issues no further requests after unmount", async () => {
    vi.useFakeTimers();
    try {
      apiRequest.mockImplementation((path) => {
        if (path.startsWith("/api/desktop-helper/status")) {
          return Promise.resolve(status({ vlc_detection_state: "detection_unavailable" }));
        }
        if (path === "/api/desktop-helper/verify") {
          return Promise.resolve({ protocol_url: "elvern-vlc-opener://verify" });
        }
        return Promise.reject(new Error(`Unexpected request: ${path}`));
      });
      const rendered = render(<InstallSettingsPanel />);
      await vi.advanceTimersByTimeAsync(0);

      fireEvent.click(screen.getByRole("button", { name: "Test helper" }));
      // Resolve the verify POST and start the polling loop.
      await vi.advanceTimersByTimeAsync(0);

      // Let the polling loop take its first status tick.
      await vi.advanceTimersByTimeAsync(900);
      const callsBeforeUnmount = apiRequest.mock.calls.length;
      expect(callsBeforeUnmount).toBeGreaterThan(1);

      rendered.unmount();
      await vi.advanceTimersByTimeAsync(3000);

      // No status requests fire after the page unmounts.
      expect(apiRequest.mock.calls.length).toBe(callsBeforeUnmount);
    } finally {
      vi.useRealTimers();
    }
  });
});
