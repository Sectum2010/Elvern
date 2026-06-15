import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { DetailPage } from "./DetailPage";


const mockAuthState = vi.hoisted(() => ({
  user: {
    id: 2,
    username: "viewer",
    role: "standard_user",
  },
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
  isIOSMobileBrowser: vi.fn(() => false),
  isHlsSessionPayload: vi.fn(() => false),
  resolveBrowserPlaybackSessionRoot: vi.fn(() => null),
}));

vi.mock("../lib/browserPlaybackPlayerState", () => ({
  resolveBrowserPlaybackPlayerViewState: vi.fn(() => ({
    browserPlaybackPreparing: false,
    playerClassName: "player",
    showMobilePreparingPlaceholder: false,
    showMobilePrewarmCard: false,
    showPlayerShell: false,
    videoControlsEnabled: true,
  })),
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
  default: () => null,
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
    selectBrowserPlaybackAudioTrack: vi.fn(),
    prepareBrowserPlaybackSubtitleTrack: vi.fn(),
    stopCurrentBrowserPlaybackSession: vi.fn(async () => {}),
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


function mockApiForDetail(item) {
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
      return Promise.resolve({
        media_library_reference_shared_default_value: "/media/shared",
        media_library_reference_private_value: "",
        media_library_reference_effective_value: "/media/shared",
      });
    }
    if (requestPath === "/api/admin/media-library-reference") {
      return Promise.resolve({
        effective_value: "/media/shared",
        default_value: "/media/shared",
      });
    }
    return Promise.reject(new Error(`Unexpected request: ${requestPath}`));
  });
}


function renderDetailPage(item) {
  mockApiForDetail(item);
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
});
