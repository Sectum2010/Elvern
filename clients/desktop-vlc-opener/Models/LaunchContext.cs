namespace Elvern.VlcOpener.Models;

public sealed class LaunchContext
{
    public string Action { get; init; } = "play";

    public string RawLaunchUrl { get; init; } = string.Empty;

    public string Scheme { get; init; } = string.Empty;

    public string ApiOrigin { get; init; } = string.Empty;

    public string HandoffId { get; init; } = string.Empty;

    public string VerificationId { get; init; } = string.Empty;

    public string AccessToken { get; init; } = string.Empty;

    public string ResolveUrl =>
        $"{ApiOrigin.TrimEnd('/')}/api/desktop-playback/handoff/{Uri.EscapeDataString(HandoffId)}?token={Uri.EscapeDataString(AccessToken)}";

    public string VerifyUrl =>
        $"{ApiOrigin.TrimEnd('/')}/api/desktop-helper/verify/{Uri.EscapeDataString(VerificationId)}?token={Uri.EscapeDataString(AccessToken)}";
}
