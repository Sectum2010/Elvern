import { act, cleanup, fireEvent, render as testingLibraryRender, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ApiNetworkError, apiRequest } from "../lib/api";
import {
  readLibraryReturnTarget,
  rememberLibraryReturnTarget,
} from "../lib/libraryNavigation";
import { DetailPage, iosExternalAppNavigator } from "./DetailPage";
import { queryClient } from "../lib/queryClient";
import { buildLibraryV2QueryKey } from "../lib/libraryQueries";
import {
  publishConnectivityRecovery,
  registerConnectivityFailure,
  resetConnectivityRecoveryStoreForTests,
} from "../lib/connectivityRecoveryStore";


const mockAuthState = vi.hoisted(() => ({
  user: {
    id: 2,
    username: "viewer",
    role: "standard_user",
  },
}));
const mockBrowserState = vi.hoisted(() => ({
  iosMobile: false,
}));
const mockPlatformState = vi.hoisted(() => ({
  clientPlatform: "unknown",
  desktopPlatform: null,
  deviceClass: "desktop",
}));
const mockBrowserPlayerViewState = vi.hoisted(() => ({
  value: {
    browserPlaybackPreparing: false,
    playerClassName: "player",
    showMobilePreparingPlaceholder: false,
    showMobilePrewarmCard: false,
    showPlayerShell: false,
    videoControlsEnabled: true,
  },
}));
const mockBrowserPlaybackControllerState = vi.hoisted(() => ({
  selectBrowserPlaybackAudioTrack: vi.fn(),
  overrides: {},
}));
const mockOverlayState = vi.hoisted(() => ({
  latestProps: null,
}));


function render(ui, options) {
  return testingLibraryRender(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
    options,
  );
}

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => mockAuthState,
}));

vi.mock("../auth/ProviderAuthContext", () => ({
  useProviderAuth: () => ({
    providerAuthRequirement: null,
    showProviderAuthPrompt: vi.fn(() => false),
    refreshProviderAuthStatus: vi.fn(async () => null),
  }),
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal()),
  apiRequest: vi.fn(),
}));

vi.mock("../lib/browserPlayback", () => ({
  getSessionModeEstimateSeconds: vi.fn(() => null),
  isIOSMobileBrowser: vi.fn(() => mockBrowserState.iosMobile),
  isHlsSessionPayload: vi.fn(() => false),
  resolveBrowserPlaybackSessionRoot: vi.fn(() => null),
}));

vi.mock("../lib/browserPlaybackPlayerState", () => ({
  resolveBrowserPlaybackPlayerViewState: vi.fn(() => mockBrowserPlayerViewState.value),
}));

vi.mock("../lib/device", () => ({
  getOrCreateDeviceId: vi.fn(() => "test-device"),
}));

vi.mock("../lib/platformDetection", () => ({
  detectClientDeviceClass: vi.fn(() => mockPlatformState.deviceClass),
  detectClientPlatform: vi.fn(() => mockPlatformState.clientPlatform),
  detectDesktopPlatform: vi.fn(() => mockPlatformState.desktopPlatform),
}));

vi.mock("../lib/playbackRouting", () => ({
  resolveDetailVlcActionRoute: vi.fn(() => ({ surface: "browser" })),
  shouldShowDesktopBrowserSeekControl: vi.fn(() => false),
  shouldShowMacAppFullscreenControl: vi.fn(() => false),
}));

vi.mock("../features/playback/ElvernPlayerOverlay", () => ({
  default: (props) => {
    mockOverlayState.latestProps = props;
    return null;
  },
  FullscreenExitIcon: ({ className }) => <svg aria-hidden="true" className={className} viewBox="0 0 24 24" />,
  InlineExpandIcon: ({ className }) => <svg aria-hidden="true" className={className} viewBox="0 0 24 24" />,
}));

vi.mock("../features/playback/useBrowserPlaybackController", () => ({
  useBrowserPlaybackController: vi.fn(() => ({
    hlsRef: { current: null },
    videoRef: { current: null },
    mobilePendingTargetRef: { current: null },
    mobileRetargetTransitionRef: { current: null },
    mobileSeekPendingRef: { current: false },
    pendingSeekPhaseRef: { current: "" },
    mobileRecoveryInFlightRef: { current: false },
    audioSwitchAttachRef: { current: null },
    mobileSession: null,
    streamSource: "",
    mobilePlayerCanPlay: false,
    mobileFrozenFrameUrl: "",
    playback: { status: "ready", reason: "", mode: "direct" },
    playbackError: "",
    seekNotice: "",
    playbackPosition: 0,
    playbackStatus: "ready",
    playbackModeIntent: "",
    hlsEngineDiagnostics: null,
    prepareEstimateObservedAtMs: 0,
    prepareEstimateNowMs: 0,
    videoElementKey: "video",
    activePlaybackMode: "",
    browserPlaybackLabel: "browser playback",
    browserPlaybackLabelTitle: "Browser Playback",
    browserStreamLabelTitle: "Browser stream",
    browserReadyLabelTitle: "Ready",
    resumePosition: 0,
    fullDuration: 1200,
    resumableStartPosition: 0,
    availableDuration: 0,
    optimizedPlaybackPending: false,
    browserPlaybackSessionActive: false,
    hasAnyBrowserPlaybackArtifacts: false,
    setPlaybackModeIntentValue: vi.fn(),
    clearPlaybackError: vi.fn(),
    clearOptimizedPlaybackPending: vi.fn(),
    prepareControllerForLoad: vi.fn(),
    clearPlaybackResources: vi.fn(),
    resetMobilePlaybackState: vi.fn(),
    syncPlaybackState: vi.fn(),
    restoreActiveBrowserPlaybackSession: vi.fn(async () => null),
    cancelBrowserPlaybackRequest: vi.fn(),
    clearPlaybackStreamSource: vi.fn(),
    setSeekNoticeValue: vi.fn(),
    setPlaybackStatusValue: vi.fn(),
    resetPendingPlaybackPreparation: vi.fn(),
    startBrowserPlaybackFrom: vi.fn(async () => {}),
    playExistingBrowserSource: vi.fn(async () => {}),
    seekBrowserPlaybackTo: vi.fn(async () => {}),
    selectBrowserPlaybackAudioTrack: mockBrowserPlaybackControllerState.selectBrowserPlaybackAudioTrack,
    prepareBrowserPlaybackSubtitleTrack: vi.fn(),
    stopCurrentBrowserPlaybackSession: vi.fn(async () => {}),
    ...mockBrowserPlaybackControllerState.overrides,
  })),
}));


function detailItem(overrides = {}) {
  return {
    id: 42,
    title: "Privacy Movie",
    parsed_title: {
      display_title: "Privacy Movie",
      base_title: "Privacy Movie",
      edition_identity: "standard",
      parsed_year: 2026,
      title_source: "title",
      parse_confidence: "high",
      warnings: [],
      parser_version: "",
      suspicious_output: false,
    },
    original_filename: "Privacy.Movie.2026.Source.Release.mkv",
    file_path: "/media/private/Privacy.Movie.2026.Source.Release.mkv",
    stream_url: "/api/stream/42",
    source_kind: "local",
    source_label: "DGX",
    library_source_name: null,
    hidden_for_user: false,
    hidden_globally: false,
    poster_url: null,
    file_size: 1024,
    duration_seconds: 1200,
    width: 1920,
    height: 1080,
    video_codec: "h264",
    audio_codec: "aac",
    container: "mkv",
    year: 2026,
    created_at: "2026-06-01T00:00:00+00:00",
    updated_at: "2026-06-01T00:00:00+00:00",
    last_scanned_at: "2026-06-01T00:00:00+00:00",
    resume_position_seconds: 0,
    subtitles: [],
    subtitle_tracks: [],
    audio_tracks: [],
    track_scan_status: "not_scanned",
    track_scan_error: "",
    track_scan_source: "not_scanned",
    audio_track_diagnostics: {},
    subtitle_track_diagnostics: {},
    age_requirement: null,
    age_requirement_display: "None",
    genres: [],
    genre_display: "Unknown",
    download_access_allowed: true,
    ...overrides,
  };
}


const defaultUserSettings = {
  media_library_reference_shared_default_value: "",
  media_library_reference_private_value: "",
  media_library_reference_effective_value: "",
  media_library_reference_effective_source: "shared_default",
  media_library_reference_effective_label: "Shared default",
};

function mockApiForDetail(item, options = {}) {
  const userSettings = {
    ...defaultUserSettings,
    ...(options.userSettings || {}),
  };
  apiRequest.mockImplementation((requestPath) => {
    if (requestPath === "/api/library/item/42") {
      return Promise.resolve(item);
    }
    if (requestPath === "/api/progress/42") {
      return Promise.resolve({
        position_seconds: 0,
        duration_seconds: item.duration_seconds,
        completed: false,
      });
    }
    if (requestPath === "/api/playback/42") {
      return Promise.resolve({ status: "ready", reason: "", mode: "direct" });
    }
    if (requestPath.startsWith("/api/desktop-playback/42?") && options.desktopPlayback) {
      return Promise.resolve(options.desktopPlayback);
    }
    if (requestPath === "/api/user-settings") {
      return Promise.resolve(userSettings);
    }
    if (requestPath === "/api/admin/media-library-reference") {
      return Promise.resolve({
        effective_value: "/media/shared",
        default_value: "/media/shared",
      });
    }
    if (requestPath === "/api/native-playback/42/session" && options.nativeSession) {
      return Promise.resolve(options.nativeSession);
    }
    return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
  });
}


function renderDetailPage(item, options = {}) {
  const {
    initialEntry = "/library/item/42",
    ...apiOptions
  } = options;
  mockApiForDetail(item, apiOptions);
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/library/item/:itemId" element={<DetailPage />} />
        <Route path="/library" element={<p>Library route</p>} />
      </Routes>
    </MemoryRouter>,
  );
}


function DetailItemSwitcher() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/library/item/43")} type="button">
      Open another item
    </button>
  );
}


async function openInfoModal() {
  const infoButton = await screen.findByRole("button", { name: "Movie info" });
  fireEvent.click(infoButton);
  await screen.findByRole("dialog", { name: "Privacy Movie" });
}


describe("DetailPage source metadata privacy", () => {
  beforeEach(() => {
    queryClient.clear();
    resetConnectivityRecoveryStoreForTests();
    window.scrollTo = vi.fn();
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    vi.restoreAllMocks();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
    mockBrowserState.iosMobile = false;
    mockPlatformState.clientPlatform = "unknown";
    mockPlatformState.desktopPlatform = null;
    mockPlatformState.deviceClass = "desktop";
    mockBrowserPlayerViewState.value = {
      browserPlaybackPreparing: false,
      playerClassName: "player",
      showMobilePreparingPlaceholder: false,
      showMobilePrewarmCard: false,
      showPlayerShell: false,
      videoControlsEnabled: true,
    };
    mockBrowserPlaybackControllerState.selectBrowserPlaybackAudioTrack = vi.fn();
    mockBrowserPlaybackControllerState.overrides = {};
    mockOverlayState.latestProps = null;
    mockAuthState.user = {
      id: 2,
      username: "viewer",
      role: "standard_user",
    };
  });

  test("standard users do not see source file metadata even if the fixture includes it", async () => {
    renderDetailPage(detailItem());

    await openInfoModal();

    expect(screen.queryByText("Source file")).not.toBeInTheDocument();
    expect(screen.queryByText("Privacy.Movie.2026.Source.Release.mkv")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lite Playback" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Full Playback" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download movie" })).toBeInTheDocument();
  });

  test("personal hidden state links to the canonical Libraries Settings section", async () => {
    renderDetailPage(detailItem({ hidden_for_user: true }));

    const hiddenLink = await screen.findByRole("link", {
      name: "Open Hidden for me in Settings",
    });
    expect(hiddenLink).toHaveAttribute("href", "/settings?section=libraries");
    expect(screen.getByText(/Settings > Libraries > Hidden for me/)).toBeInTheDocument();
  });

  test("global hidden state links admins to the canonical Libraries Settings section", async () => {
    mockAuthState.user = { id: 2, username: "admin", role: "admin" };
    renderDetailPage(detailItem({ hidden_globally: true }));

    const hiddenLink = await screen.findByRole("link", {
      name: "Open Hidden for everyone in Settings",
    });
    expect(hiddenLink).toHaveAttribute("href", "/settings?section=libraries");
  });

  test("resets detail scroll once per item before async detail content renders", async () => {
    window.scrollTo = vi.fn();
    Object.defineProperty(window, "scrollY", { configurable: true, value: 640 });
    mockApiForDetail(detailItem());
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <DetailItemSwitcher />
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(window.scrollTo).toHaveBeenCalledTimes(1);
    expect(window.scrollTo).toHaveBeenLastCalledWith({
      top: 0,
      left: 0,
      behavior: "auto",
    });

    await screen.findByText("Privacy Movie");
    expect(window.scrollTo).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Open another item" }));
    expect(window.scrollTo).toHaveBeenCalledTimes(2);
  });

  test("keeps the detail heading before the player card", async () => {
    renderDetailPage(detailItem());

    const heading = await screen.findByRole("heading", { level: 1, name: "Privacy Movie" });
    const playerCard = document.querySelector(".player-card");

    expect(playerCard).not.toBeNull();
    expect(Boolean(heading.compareDocumentPosition(playerCard) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });

  test("renders a cached v2 preview and player shell before detail metadata resolves", async () => {
    let resolveItem;
    const itemPromise = new Promise((resolve) => {
      resolveItem = resolve;
    });
    queryClient.setQueryData(buildLibraryV2QueryKey({
      userId: 2,
      role: "standard_user",
      category: "movies",
      source: "all",
      quality: "all",
      sort: "smart",
    }), {
      schema_version: "library-summary-v2",
      items_by_id: {
        "42": {
          id: 42,
          title: "Cached Preview Movie",
          year: 2025,
          source_kind: "local",
        },
      },
    });
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/library/item/42") {
        return itemPromise;
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request before metadata: ${requestPath}`));
    });

    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Cached Preview Movie" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lite Playback" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Full Playback" })).toBeInTheDocument();
    expect(screen.queryByText(/Diamond|Gold|Silver|Bronze/)).not.toBeInTheDocument();

    await act(async () => {
      resolveItem(detailItem({ title: "Authoritative Movie", parsed_title: null }));
      await itemPromise;
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Authoritative Movie" })).toBeInTheDocument();
  });

  test("keeps a cached preview through a transport failure and retries once on recovery", async () => {
    const failure = registerConnectivityFailure();
    queryClient.setQueryData(buildLibraryV2QueryKey({
      userId: 2,
      role: "standard_user",
      category: "movies",
      source: "all",
      quality: "all",
      sort: "smart",
    }), {
      schema_version: "library-summary-v2",
      items_by_id: {
        "42": {
          id: 42,
          title: "Cached Preview Movie",
          year: 2025,
          source_kind: "local",
        },
      },
    });
    let itemCalls = 0;
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/library/item/42") {
        itemCalls += 1;
        return itemCalls === 1
          ? Promise.reject(new ApiNetworkError(undefined, {
            cause: new TypeError("NetworkError when attempting to fetch resource"),
            ...failure,
          }))
          : Promise.resolve(detailItem({ title: "Recovered Movie", parsed_title: null }));
      }
      if (requestPath === "/api/progress/42") {
        return Promise.resolve({
          position_seconds: 0,
          duration_seconds: 1200,
          completed: false,
        });
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Cached Preview Movie" })).toBeInTheDocument();
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();
    expect(screen.queryByText(/NetworkError when attempting/i)).not.toBeInTheDocument();

    const recoveryEvent = {
      generation: 21,
      recoveredThroughFailureId: failure.failureId,
    };
    publishConnectivityRecovery(recoveryEvent);
    expect(await screen.findByRole("heading", { level: 1, name: "Recovered Movie" })).toBeInTheDocument();
    expect(itemCalls).toBe(2);

    publishConnectivityRecovery(recoveryEvent);
    await act(async () => Promise.resolve());
    expect(itemCalls).toBe(2);
  });

  test("metadata-first recovery starts every applicable auxiliary read only after metadata succeeds", async () => {
    mockAuthState.user = { id: 2, username: "admin", role: "admin" };
    mockPlatformState.desktopPlatform = "linux";
    const restoreSession = vi.fn(async () => null);
    mockBrowserPlaybackControllerState.overrides = {
      restoreActiveBrowserPlaybackSession: restoreSession,
    };
    const failure = registerConnectivityFailure();
    const counts = {
      metadata: 0,
      progress: 0,
      playback: 0,
      desktop: 0,
      cloud: 0,
      admin: 0,
    };
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      if (requestPath === "/api/library/item/42") {
        counts.metadata += 1;
        return counts.metadata === 1
          ? Promise.reject(new ApiNetworkError(undefined, failure))
          : Promise.resolve(detailItem({ source_kind: "cloud" }));
      }
      if (requestPath === "/api/progress/42") {
        counts.progress += 1;
        return Promise.resolve({ position_seconds: 0, duration_seconds: 1200, completed: false });
      }
      if (requestPath === "/api/playback/42") {
        counts.playback += 1;
        return Promise.resolve({ status: "ready", reason: "", mode: "direct" });
      }
      if (requestPath.startsWith("/api/desktop-playback/42?")) {
        counts.desktop += 1;
        return Promise.resolve({ supported: true });
      }
      if (requestPath === "/api/cloud-libraries") {
        counts.cloud += 1;
        return Promise.resolve({ libraries: [] });
      }
      if (requestPath === "/api/admin/media-library-reference") {
        counts.admin += 1;
        return Promise.resolve({ effective_value: "/media/reference" });
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(counts).toEqual({
      metadata: 1,
      progress: 0,
      playback: 0,
      desktop: 0,
      cloud: 0,
      admin: 0,
    });
    expect(restoreSession).not.toHaveBeenCalled();

    publishConnectivityRecovery({
      generation: 22,
      recoveredThroughFailureId: failure.failureId,
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Privacy Movie" })).toBeInTheDocument();
    await waitFor(() => expect(counts).toEqual({
      metadata: 2,
      progress: 1,
      playback: 1,
      desktop: 1,
      cloud: 1,
      admin: 1,
    }));
    expect(restoreSession).toHaveBeenCalledTimes(1);
  });

  test("initial detail transport failure exposes a stable manual retry", async () => {
    const failure = registerConnectivityFailure();
    let itemCalls = 0;
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/library/item/42") {
        itemCalls += 1;
        return itemCalls === 1
          ? Promise.reject(new ApiNetworkError(undefined, failure))
          : Promise.resolve(detailItem());
      }
      if (requestPath === "/api/progress/42") {
        return Promise.resolve({
          position_seconds: 0,
          duration_seconds: 1200,
          completed: false,
        });
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Privacy Movie" })).toBeInTheDocument();
    expect(itemCalls).toBe(2);
  });

  test.each([403, 404])("HTTP %s metadata failure is not retried after connectivity recovery", async (statusCode) => {
    let itemCalls = 0;
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/library/item/42") {
        itemCalls += 1;
        const error = new Error("Detail is unavailable");
        error.status = statusCode;
        return Promise.reject(error);
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Detail is unavailable")).toBeInTheDocument();
    const unrelatedFailure = registerConnectivityFailure();
    publishConnectivityRecovery({
      generation: 42,
      recoveredThroughFailureId: unrelatedFailure.failureId,
    });
    await act(async () => Promise.resolve());
    expect(itemCalls).toBe(1);
  });

  test("recovers a transient progress failure by retrying only progress, not healthy metadata or playback", async () => {
    const failure = registerConnectivityFailure();
    let itemCalls = 0;
    let progressCalls = 0;
    let playbackCalls = 0;
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/library/item/42") {
        itemCalls += 1;
        return Promise.resolve(detailItem());
      }
      if (requestPath === "/api/progress/42") {
        progressCalls += 1;
        return progressCalls === 1
          ? Promise.reject(new ApiNetworkError(undefined, {
            cause: new TypeError("NetworkError when attempting to fetch resource"),
            ...failure,
          }))
          : Promise.resolve({ position_seconds: 42, duration_seconds: 1200, completed: false });
      }
      if (requestPath === "/api/playback/42") {
        playbackCalls += 1;
        return Promise.resolve({ status: "ready", reason: "", mode: "direct" });
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Privacy Movie" })).toBeInTheDocument();
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();
    expect(screen.queryByText(/NetworkError when attempting/i)).not.toBeInTheDocument();

    const recovery = {
      generation: 7,
      recoveredThroughFailureId: failure.failureId,
    };
    publishConnectivityRecovery(recovery);
    await waitFor(() => expect(progressCalls).toBe(2));
    await waitFor(() => expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument());
    // Metadata and the healthy playback capability are not re-fetched.
    expect(itemCalls).toBe(1);
    expect(playbackCalls).toBe(1);

    publishConnectivityRecovery(recovery);
    await act(async () => Promise.resolve());
    expect(progressCalls).toBe(2);
  });

  test("recovers a transient playback-capability failure by retrying it once on recovery", async () => {
    const failure = registerConnectivityFailure();
    let itemCalls = 0;
    let playbackCalls = 0;
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/library/item/42") {
        itemCalls += 1;
        return Promise.resolve(detailItem());
      }
      if (requestPath === "/api/progress/42") {
        return Promise.resolve({ position_seconds: 0, duration_seconds: 1200, completed: false });
      }
      if (requestPath === "/api/playback/42") {
        playbackCalls += 1;
        return playbackCalls === 1
          ? Promise.reject(new ApiNetworkError(undefined, failure))
          : Promise.resolve({ status: "ready", reason: "", mode: "direct" });
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Privacy Movie" })).toBeInTheDocument();
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();

    publishConnectivityRecovery({
      generation: 9,
      recoveredThroughFailureId: failure.failureId,
    });
    await waitFor(() => expect(playbackCalls).toBe(2));
    await waitFor(() => expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument());
    expect(itemCalls).toBe(1);
  });

  test("cloud health and admin reference transient failures retry selectively and clear recovery UI", async () => {
    mockAuthState.user = { id: 2, username: "admin", role: "admin" };
    const failure = registerConnectivityFailure();
    let metadataCalls = 0;
    let cloudCalls = 0;
    let adminCalls = 0;
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/library/item/42") {
        metadataCalls += 1;
        return Promise.resolve(detailItem({ source_kind: "cloud" }));
      }
      if (requestPath === "/api/progress/42") {
        return Promise.resolve({ position_seconds: 0, duration_seconds: 1200, completed: false });
      }
      if (requestPath === "/api/playback/42") {
        return Promise.resolve({ status: "ready", reason: "", mode: "direct" });
      }
      if (requestPath === "/api/cloud-libraries") {
        cloudCalls += 1;
        return cloudCalls === 1
          ? Promise.reject(new ApiNetworkError(undefined, failure))
          : Promise.resolve({ libraries: [] });
      }
      if (requestPath === "/api/admin/media-library-reference") {
        adminCalls += 1;
        return adminCalls === 1
          ? Promise.reject(new ApiNetworkError(undefined, failure))
          : Promise.resolve({ effective_value: "/media/reference" });
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();
    publishConnectivityRecovery({
      generation: 23,
      recoveredThroughFailureId: failure.failureId,
    });
    await waitFor(() => expect(cloudCalls).toBe(2));
    await waitFor(() => expect(adminCalls).toBe(2));
    await waitFor(() => expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument());
    expect(metadataCalls).toBe(1);
  });

  test("an HTTP business error on an auxiliary read is never treated as a network recovery", async () => {
    let progressCalls = 0;
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/library/item/42") {
        return Promise.resolve(detailItem());
      }
      if (requestPath === "/api/progress/42") {
        progressCalls += 1;
        const error = new Error("Progress is forbidden");
        error.status = 403;
        return Promise.reject(error);
      }
      if (requestPath === "/api/playback/42") {
        return Promise.resolve({ status: "ready", reason: "", mode: "direct" });
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Privacy Movie" })).toBeInTheDocument();
    await waitFor(() => expect(progressCalls).toBe(1));
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();

    const unrelatedFailure = registerConnectivityFailure();
    publishConnectivityRecovery({
      generation: 11,
      recoveredThroughFailureId: unrelatedFailure.failureId,
    });
    await act(async () => Promise.resolve());
    expect(progressCalls).toBe(1);
  });

  test("aborts the previous metadata request when the item changes", async () => {
    const itemSignals = [];
    apiRequest.mockImplementation((requestPath, options = {}) => {
      if (requestPath.startsWith("/api/library/item/")) {
        itemSignals.push(options.signal);
        return new Promise(() => {});
      }
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultUserSettings);
      }
      return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
    });
    render(
      <MemoryRouter initialEntries={["/library/item/42"]}>
        <DetailItemSwitcher />
        <Routes>
          <Route path="/library/item/:itemId" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(itemSignals).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Open another item" }));
    await waitFor(() => expect(itemSignals).toHaveLength(2));

    expect(itemSignals[0]).toBeInstanceOf(AbortSignal);
    expect(itemSignals[0].aborted).toBe(true);
    expect(itemSignals[1].aborted).toBe(false);
  });

  test("preserves the complete library relocation target when preparing the return", async () => {
    const target = rememberLibraryReturnTarget({
      listPath: "/library?category=anime&q=akira",
      anchorItemId: 42,
      anchorInstanceKey: "series:akira:42",
      scrollY: 912,
      pendingRestore: false,
      anchorViewportRatioY: 0.27,
      anchorViewportRatioX: 0.18,
      viewportWidth: 1440,
      viewportHeight: 900,
      railKey: "series:akira",
      railScrollLeft: 288,
    });
    renderDetailPage(detailItem(), {
      initialEntry: {
        pathname: "/library/item/42",
        state: { libraryReturn: target },
      },
    });

    await screen.findByRole("heading", { level: 1, name: "Privacy Movie" });
    fireEvent.click(await screen.findByRole("link", { name: "Back to library" }));

    expect(readLibraryReturnTarget()).toEqual({
      ...target,
      pendingRestore: true,
    });
  });

  test("removes redundant desktop helper guidance while keeping notes and the real fallback", async () => {
    mockPlatformState.clientPlatform = "windows";
    mockPlatformState.desktopPlatform = "windows";
    renderDetailPage(detailItem(), {
      desktopPlayback: {
        platform: "windows",
        open_method: "protocol_helper",
        same_host_launch: false,
        open_supported: false,
        handoff_supported: false,
        used_backend_fallback: true,
        notes: ["Desktop helper handoff is ready for this device."],
        playlist_url: "/api/desktop-playback/42/playlist",
        vlc_target: "safe-test-target",
      },
    });

    expect(await screen.findByText("Desktop helper handoff is ready for this device.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Helper Setup" })).not.toBeInTheDocument();
    expect(screen.queryByText(/direct path mapping is not configured/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download VLC Playlist" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy VLC Target" })).toBeInTheDocument();
  });

  test("standard users see private reference and safe effective labels without shared default path", async () => {
    renderDetailPage(detailItem(), {
      userSettings: {
        media_library_reference_shared_default_value: "",
        media_library_reference_private_value: "",
        media_library_reference_effective_value: "",
        media_library_reference_effective_source: "shared_default",
        media_library_reference_effective_label: "Shared default",
      },
    });

    await openInfoModal();
    const dialog = screen.getByRole("dialog", { name: "Privacy Movie" });

    expect(within(dialog).queryByText("Shared default", { selector: ".detail-list span" })).not.toBeInTheDocument();
    expect(within(dialog).getByText("My private reference")).toBeInTheDocument();
    expect(within(dialog).getByText("Not set")).toBeInTheDocument();
    expect(within(dialog).getByText("Using now")).toBeInTheDocument();
    expect(within(dialog).getByText("Shared default")).toBeInTheDocument();
    expect(within(dialog).queryByText("/media/shared")).not.toBeInTheDocument();
  });

  test("standard users see their private reference and a plain effective source label", async () => {
    renderDetailPage(detailItem(), {
      userSettings: {
        media_library_reference_shared_default_value: "",
        media_library_reference_private_value: "Alice Shelf A",
        media_library_reference_effective_value: "Alice Shelf A",
        media_library_reference_effective_source: "private_reference",
        media_library_reference_effective_label: "My private reference",
      },
    });

    await openInfoModal();
    const dialog = screen.getByRole("dialog", { name: "Privacy Movie" });

    expect(within(dialog).queryByText("Shared default", { selector: ".detail-list span" })).not.toBeInTheDocument();
    expect(within(dialog).getByText("Alice Shelf A")).toBeInTheDocument();
    expect(within(dialog).getByText("My private reference", { selector: "p" })).toBeInTheDocument();
    expect(within(dialog).queryByText("/media/shared")).not.toBeInTheDocument();
  });

  test("admins still see source file metadata in the info modal", async () => {
    mockAuthState.user = {
      id: 1,
      username: "admin",
      role: "admin",
    };
    renderDetailPage(detailItem());

    await openInfoModal();

    expect(screen.getByText("Source file")).toBeInTheDocument();
    expect(screen.getByText("Privacy.Movie.2026.Source.Release.mkv")).toBeInTheDocument();
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/admin/media-library-reference",
      expect.objectContaining({ abortOnPageHide: true }),
    ));
  });

  test("Infuse callback restores fallback URL into React state and clears sessionStorage", async () => {
    mockBrowserState.iosMobile = true;
    const playbackUrl = "https://elvern.test/api/native-playback/session/s1/stream?token=secret";
    const launchUrl = `infuse://x-callback-url/play?url=${encodeURIComponent(playbackUrl)}`;
    window.sessionStorage.setItem(
      "elvern-ios-handoff:42:infuse",
      JSON.stringify({
        itemId: "42",
        app: "infuse",
        launchUrl,
        playbackUrl,
        savedAt: Date.now(),
      }),
    );
    window.history.replaceState({}, "", "/library/item/42?ios_app=infuse&ios_result=error&errorMessage=Nope");

    renderDetailPage(detailItem());

    expect(await screen.findByDisplayValue(playbackUrl)).toBeInTheDocument();
    expect(screen.getByText("Infuse handoff failed: Nope")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("elvern-ios-handoff:42:infuse")).toBeNull();
  });

  test("Infuse launch stores only the guarded fallback handoff state", async () => {
    const assignSpy = vi.spyOn(iosExternalAppNavigator, "assign").mockImplementation(() => {});
    mockBrowserState.iosMobile = true;
    const playbackUrl = "https://elvern.test/api/native-playback/session/s1/stream?token=secret";

    renderDetailPage(detailItem(), {
      nativeSession: {
        session_id: "s1",
        stream_url: playbackUrl,
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Open in Infuse (Pro)" }));

    await waitFor(() => {
      expect(window.sessionStorage.getItem("elvern-ios-handoff:42:infuse")).not.toBeNull();
    });
    const saved = JSON.parse(window.sessionStorage.getItem("elvern-ios-handoff:42:infuse"));
    expect(saved).toMatchObject({
      itemId: "42",
      app: "infuse",
      playbackUrl,
    });
    expect(saved.launchUrl).toContain("infuse://x-callback-url/play?");
    expect(assignSpy).toHaveBeenCalledWith(saved.launchUrl);
  });

  test("VLC launch does not store an iOS handoff fallback", async () => {
    const assignSpy = vi.spyOn(iosExternalAppNavigator, "assign").mockImplementation(() => {});
    mockBrowserState.iosMobile = true;
    const playbackUrl = "https://elvern.test/api/native-playback/session/s1/stream?token=secret";

    renderDetailPage(detailItem(), {
      nativeSession: {
        session_id: "s1",
        stream_url: playbackUrl,
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Open in VLC" }));

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/native-playback/42/session",
      expect.objectContaining({
        data: expect.objectContaining({ external_player: "vlc" }),
      }),
    ));
    expect(window.sessionStorage.getItem("elvern-ios-handoff:42:infuse")).toBeNull();
    expect(window.sessionStorage.getItem("elvern-ios-handoff:42:vlc")).toBeNull();
    expect(assignSpy).toHaveBeenCalledWith(expect.stringContaining("vlc-x-callback://x-callback-url/stream?"));
  });

  test("VLC callback does not use Infuse sessionStorage fallback", async () => {
    mockBrowserState.iosMobile = true;
    window.history.replaceState({}, "", "/library/item/42?ios_app=vlc&ios_result=error");

    renderDetailPage(detailItem());

    await screen.findByText("VLC could not continue this handoff. Try the VLC button again.");
    expect(window.sessionStorage.getItem("elvern-ios-handoff:42:vlc")).toBeNull();
    expect(screen.queryByText("Copy short-lived playback URL")).not.toBeInTheDocument();
  });

  test("passes browser audio selector from controller into Elvern overlay", async () => {
    const selectBrowserPlaybackAudioTrack = vi.fn();
    mockBrowserPlaybackControllerState.selectBrowserPlaybackAudioTrack = selectBrowserPlaybackAudioTrack;
    mockBrowserPlayerViewState.value = {
      browserPlaybackPreparing: false,
      playerClassName: "player",
      showMobilePreparingPlaceholder: false,
      showMobilePrewarmCard: false,
      showPlayerShell: true,
      videoControlsEnabled: false,
    };

    renderDetailPage(detailItem({
      audio_tracks: [
        { index: 1, label: "English", codec: "aac", track_source: "raw_probe_summary_json" },
      ],
    }));

    await waitFor(() => {
      expect(mockOverlayState.latestProps).not.toBeNull();
    });
    expect(mockOverlayState.latestProps.onBackendAudioTrackSelect).toBe(selectBrowserPlaybackAudioTrack);
  });

  test("browser preparing state renders only the player prewarm card and keeps Prepared through wording", async () => {
    mockBrowserPlayerViewState.value = {
      browserPlaybackPreparing: true,
      playerClassName: "player",
      showMobilePreparingPlaceholder: true,
      showMobilePrewarmCard: true,
      showPlayerShell: true,
      videoControlsEnabled: false,
    };
    mockBrowserPlaybackControllerState.overrides = {
      activePlaybackMode: "lite",
      browserPlaybackLabel: "lite playback",
      browserPlaybackSessionActive: true,
      fullDuration: 1200,
      mobileSession: {
        attach_ready: true,
        duration_seconds: 1200,
        engine_mode: "route2",
        playback_mode: "lite",
        ready_end_seconds: 4,
        target_position_seconds: 0,
      },
      optimizedPlaybackPending: true,
      prepareEstimateObservedAtMs: 0,
      prepareEstimateNowMs: 0,
    };

    renderDetailPage(detailItem());

    expect(await screen.findByText("Preparing lite playback")).toBeInTheDocument();
    expect(document.querySelector(".player-prewarm-card")).not.toBeNull();
    expect(document.querySelector(".playback-pending-indicator")).toBeNull();
    expect(mockOverlayState.latestProps).toBeNull();
    expect(screen.getByRole("button", { name: "Maximize player" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Maximize player" })).toHaveClass("elvern-overlay__inline-maximize");
    expect(screen.getByRole("button", { name: "Maximize player" }).querySelector(".elvern-overlay__inline-maximize-icon")).not.toBeNull();
    expect(screen.getByText("EST --:--")).toBeInTheDocument();
    expect(screen.getByText("Prepared through 0:00 of 20:00.")).toBeInTheDocument();
    expect(screen.queryByText("Preparing reusable cached media around 0:00.")).not.toBeInTheDocument();
    expect(screen.queryByText("Preparing 0:00...")).not.toBeInTheDocument();
    expect(screen.queryByText(/client buffer/i)).not.toBeInTheDocument();
  });

  test("browser prewarm card escape button toggles between normal and cinema layout without overlay", async () => {
    mockBrowserPlayerViewState.value = {
      browserPlaybackPreparing: true,
      playerClassName: "player",
      showMobilePreparingPlaceholder: true,
      showMobilePrewarmCard: true,
      showPlayerShell: true,
      videoControlsEnabled: false,
    };
    mockBrowserPlaybackControllerState.overrides = {
      activePlaybackMode: "lite",
      browserPlaybackLabel: "lite playback",
      browserPlaybackSessionActive: true,
      fullDuration: 1200,
      mobileSession: {
        attach_ready: true,
        duration_seconds: 1200,
        engine_mode: "route2",
        playback_mode: "lite",
        ready_end_seconds: 4,
        target_position_seconds: 0,
      },
      optimizedPlaybackPending: true,
      prepareEstimateObservedAtMs: 0,
      prepareEstimateNowMs: 0,
    };

    renderDetailPage(detailItem());

    const maximizeButton = await screen.findByRole("button", { name: "Maximize player" });
    expect(mockOverlayState.latestProps).toBeNull();

    fireEvent.click(maximizeButton);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Minimize player" })).toBeInTheDocument();
    });
    expect(document.querySelector(".player-shell--cinema-takeover")).not.toBeNull();
    expect(mockOverlayState.latestProps).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Minimize player" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Maximize player" })).toBeInTheDocument();
    });
    expect(document.querySelector(".player-shell--cinema-takeover")).toBeNull();
    expect(screen.getByText("Prepared through 0:00 of 20:00.")).toBeInTheDocument();
  });
	});
