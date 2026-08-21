export function collectPlaybackDiagnosticCapabilities({
  windowRef = globalThis.window,
  documentRef = globalThis.document,
  navigatorRef = globalThis.navigator,
  video = null,
} = {}) {
  const performanceRef = windowRef?.performance || globalThis.performance;
  return {
    indexeddb: Boolean(windowRef?.indexedDB || globalThis.indexedDB),
    request_video_frame_callback: typeof video?.requestVideoFrameCallback === "function",
    video_playback_quality: typeof video?.getVideoPlaybackQuality === "function",
    performance_observer: typeof windowRef?.PerformanceObserver === "function",
    resource_timing: Boolean(performanceRef?.getEntriesByType),
    long_task_timing: Boolean(windowRef?.PerformanceObserver?.supportedEntryTypes?.includes?.("longtask")),
    long_animation_frame_timing: Boolean(windowRef?.PerformanceObserver?.supportedEntryTypes?.includes?.("long-animation-frame")),
    performance_memory: Boolean(performanceRef?.memory),
    user_agent_specific_memory: typeof performanceRef?.measureUserAgentSpecificMemory === "function",
    compute_pressure: typeof windowRef?.PressureObserver === "function",
    storage_estimate: typeof navigatorRef?.storage?.estimate === "function",
    device_memory: Number.isFinite(Number(navigatorRef?.deviceMemory)),
    network_information: Boolean(navigatorRef?.connection),
    document_was_discarded: "wasDiscarded" in (documentRef || {}),
    freeze_resume_events: Boolean(documentRef?.addEventListener),
    picture_in_picture: Boolean(documentRef?.pictureInPictureEnabled),
    fullscreen: Boolean(documentRef?.fullscreenEnabled),
    native_hls_internal_cache: false,
    client_fragment_loader_detail: false,
    server_segment_request_trace: true,
  };
}

export function unsupportedWebCapabilities(capabilities) {
  const unavailable = [
    "safari_process_rss",
    "iphone_free_physical_ram",
    "browser_media_process_ram",
    "native_hls_internal_cache_bytes",
    "total_browser_http_cache_bytes",
    "exact_browser_cpu_percent",
    "ios_memory_pressure_numeric_level",
    "other_app_memory_usage",
    "os_audio_output_clock",
  ];
  if (!capabilities.request_video_frame_callback) unavailable.push("request_video_frame_callback");
  if (!capabilities.video_playback_quality) unavailable.push("video_playback_quality");
  if (!capabilities.performance_memory) unavailable.push("javascript_heap_memory");
  if (!capabilities.compute_pressure) unavailable.push("compute_pressure");
  return unavailable;
}
