using System.Text.Json.Serialization;

namespace Elvern.VlcOpener.Models;

public sealed class DesktopVlcHandoff
{
    [JsonPropertyName("handoff_id")]
    public string HandoffId { get; init; } = string.Empty;

    [JsonPropertyName("title")]
    public string Title { get; init; } = string.Empty;

    [JsonPropertyName("media_id")]
    public int MediaId { get; init; }

    [JsonPropertyName("platform")]
    public string Platform { get; init; } = string.Empty;

    [JsonPropertyName("strategy")]
    public string Strategy { get; init; } = string.Empty;

    [JsonPropertyName("target_kind")]
    public string TargetKind { get; init; } = string.Empty;

    [JsonPropertyName("target")]
    public string Target { get; init; } = string.Empty;

    [JsonPropertyName("started_url")]
    public string? StartedUrl { get; init; }

    [JsonPropertyName("resume_seconds")]
    public double ResumeSeconds { get; init; }

    [JsonPropertyName("expires_at")]
    public string ExpiresAt { get; init; } = string.Empty;
}
