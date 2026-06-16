import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { DetailPage, iosExternalAppNavigator } from "./DetailPage";


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

vi.mock("../lib/api", () => ({
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
  detectClientDeviceClass: vi.fn(() => "desktop"),
  detectClientPlatform: vi.fn(() => "desktop"),
  detectDesktopPlatform: vi.fn(() => ""),
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
}));

vi.mock("../features/playback/useBrowserPlaybackController", () => ({
  useBrowserPlaybackController: vi.fn(() => ({
    hlsRef: { current: null },
    videoRef: { current: null },
    mobilePendingTargetRef: { current: null },
    mobileRetargetTransitionRef: { current: null },
    mobileSeekPendingRef: { current: false },
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
  mockApiForDetail(item, options);
  render(
    <MemoryRouter initialEntries={["/library/item/42"]}>
      <Routes>
        <Route path="/library/item/:itemId" element={<DetailPage />} />
        <Route path="/library" element={<p>Library route</p>} />
      </Routes>
    </MemoryRouter>,
  );
}


async function openInfoModal() {
  const infoButton = await screen.findByRole("button", { name: "Movie info" });
  fireEvent.click(infoButton);
  await screen.findByRole("dialog", { name: "Privacy Movie" });
}


describe("DetailPage source metadata privacy", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
    mockBrowserState.iosMobile = false;
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
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/admin/media-library-reference"));
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
    expect(screen.getByText("EST --:--")).toBeInTheDocument();
    expect(screen.getByText("Prepared through 0:00 of 20:00.")).toBeInTheDocument();
    expect(screen.queryByText(/client buffer/i)).not.toBeInTheDocument();
  });
});
