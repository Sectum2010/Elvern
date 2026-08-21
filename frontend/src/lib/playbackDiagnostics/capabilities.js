export function collectPlaybackDiagnosticCapabilities({
  windowRef = globalThis.window,
  documentRef = globalThis.document,
  navigatorRef = globalThis.navigator,
  video = null,
} = {}) {
  const performanceRef = windowRef?.performance || globalThis.performance;
  const state = (available, collected = true) => (
    available ? (collected ? "api_detected" : "detected_not_collected") : "api_absent"
  );
  const lifecycleFreezeSupported = Boolean(documentRef && "onfreeze" in documentRef);
  return {
    indexeddb: state(Boolean(windowRef?.indexedDB || globalThis.indexedDB)),
    request_video_frame_callback: state(typeof video?.requestVideoFrameCallback === "function"),
    video_playback_quality: state(typeof video?.getVideoPlaybackQuality === "function"),
    performance_observer: state(typeof windowRef?.PerformanceObserver === "function"),
    resource_timing: state(Boolean(performanceRef?.getEntriesByType)),
    long_task_timing: state(Boolean(windowRef?.PerformanceObserver?.supportedEntryTypes?.includes?.("longtask"))),
    long_animation_frame_timing: state(Boolean(windowRef?.PerformanceObserver?.supportedEntryTypes?.includes?.("long-animation-frame"))),
    performance_memory: state(Boolean(performanceRef?.memory)),
    user_agent_specific_memory: state(
      typeof performanceRef?.measureUserAgentSpecificMemory === "function",
      false,
    ),
    compute_pressure: state(typeof windowRef?.PressureObserver === "function", false),
    storage_estimate: state(typeof navigatorRef?.storage?.estimate === "function"),
    device_memory: state(Number.isFinite(Number(navigatorRef?.deviceMemory))),
    network_information: state(Boolean(navigatorRef?.connection), false),
    document_was_discarded: state("wasDiscarded" in (documentRef || {})),
    freeze_resume_events: state(lifecycleFreezeSupported),
    picture_in_picture: state(Boolean(documentRef?.pictureInPictureEnabled)),
    fullscreen: state(Boolean(documentRef?.fullscreenEnabled)),
    native_hls_internal_cache: "api_absent",
    client_fragment_loader_detail: "not_applicable",
    server_segment_request_trace: "server_collected",
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
  if (capabilities.request_video_frame_callback === "api_absent") unavailable.push("request_video_frame_callback");
  if (capabilities.video_playback_quality === "api_absent") unavailable.push("video_playback_quality");
  if (capabilities.performance_memory === "api_absent") unavailable.push("javascript_heap_memory");
  if (capabilities.compute_pressure === "api_absent") unavailable.push("compute_pressure");
  return unavailable;
}
