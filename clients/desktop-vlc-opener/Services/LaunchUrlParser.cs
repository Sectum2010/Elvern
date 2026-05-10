using Elvern.VlcOpener.Models;

namespace Elvern.VlcOpener.Services;

public static class LaunchUrlParser
{
    public static LaunchContext Parse(string rawArgument, string? allowedOriginOverride = null)
    {
        var raw = NormalizeRawArgument(rawArgument);
        if (string.IsNullOrWhiteSpace(raw))
        {
            throw new InvalidOperationException("Invalid Elvern VLC handoff URL: launch argument is empty.");
        }

        if (!Uri.TryCreate(raw, UriKind.Absolute, out var uri))
        {
            throw new InvalidOperationException("Invalid Elvern VLC handoff URL: the launch string is not a valid absolute URI.");
        }

        var action = string.IsNullOrWhiteSpace(uri.Host)
            ? uri.AbsolutePath.Trim('/').ToLowerInvariant()
            : uri.Host.ToLowerInvariant();

        if (!string.Equals(action, "play", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(action, "verify", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Invalid Elvern VLC helper URL: only the play and verify actions are supported.");
        }

        var query = ParseQueryString(uri.Query);
        var allowedOrigin = HelperOriginPolicy.ResolveAllowedOrigin(allowedOriginOverride);
        var apiOrigin = query.TryGetValue("api", out var providedApiOrigin)
            && !string.IsNullOrWhiteSpace(providedApiOrigin)
            ? providedApiOrigin
            : allowedOrigin;
        var accessToken = GetRequiredQueryValue(query, "token");
        var handoffId = string.Equals(action, "play", StringComparison.OrdinalIgnoreCase)
            ? GetRequiredQueryValue(query, "handoff")
            : string.Empty;
        var verificationId = string.Equals(action, "verify", StringComparison.OrdinalIgnoreCase)
            ? GetRequiredQueryValue(query, "verification")
            : string.Empty;

        if (!Uri.TryCreate(apiOrigin, UriKind.Absolute, out var apiUri)
            || (apiUri.Scheme != Uri.UriSchemeHttp && apiUri.Scheme != Uri.UriSchemeHttps))
        {
            throw new InvalidOperationException("Invalid Elvern VLC handoff URL: api must be an absolute http(s) origin.");
        }
        var normalizedApiOrigin = apiUri.GetLeftPart(UriPartial.Authority);
        if (!HelperOriginPolicy.OriginsMatch(normalizedApiOrigin, allowedOrigin))
        {
            OpenerLog.Info(
                $"helper_origin_rejected allowed={allowedOrigin} attempted={normalizedApiOrigin}");
            throw new InvalidOperationException("Invalid Elvern VLC handoff URL: api origin is not allowed by this helper build.");
        }

        return new LaunchContext
        {
            Action = action,
            RawLaunchUrl = raw,
            Scheme = uri.Scheme,
            ApiOrigin = normalizedApiOrigin,
            HandoffId = handoffId,
            VerificationId = verificationId,
            AccessToken = accessToken,
        };
    }

    private static string NormalizeRawArgument(string? rawArgument)
    {
        var raw = (rawArgument ?? string.Empty).Trim().Trim('"');
        if (raw.StartsWith("URL:", StringComparison.OrdinalIgnoreCase))
        {
            raw = raw[4..].Trim();
        }

        if (raw.StartsWith("elvern-vlc:play?", StringComparison.OrdinalIgnoreCase))
        {
            raw = $"elvern-vlc://play?{raw["elvern-vlc:play?".Length..]}";
        }
        else if (raw.StartsWith("elvern-vlc:verify?", StringComparison.OrdinalIgnoreCase))
        {
            raw = $"elvern-vlc://verify?{raw["elvern-vlc:verify?".Length..]}";
        }
        else if (raw.StartsWith("elvern-vlc:/play?", StringComparison.OrdinalIgnoreCase)
                 && !raw.StartsWith("elvern-vlc://play?", StringComparison.OrdinalIgnoreCase))
        {
            raw = $"elvern-vlc://play?{raw["elvern-vlc:/play?".Length..]}";
        }
        else if (raw.StartsWith("elvern-vlc:/verify?", StringComparison.OrdinalIgnoreCase)
                 && !raw.StartsWith("elvern-vlc://verify?", StringComparison.OrdinalIgnoreCase))
        {
            raw = $"elvern-vlc://verify?{raw["elvern-vlc:/verify?".Length..]}";
        }

        return raw;
    }

    private static IReadOnlyDictionary<string, string> ParseQueryString(string query)
    {
        var trimmed = query.TrimStart('?');
        var pairs = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        if (string.IsNullOrEmpty(trimmed))
        {
            return pairs;
        }

        foreach (var segment in trimmed.Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var pieces = segment.Split('=', 2);
            var key = Uri.UnescapeDataString(pieces[0]);
            var value = pieces.Length > 1 ? Uri.UnescapeDataString(pieces[1]) : string.Empty;
            pairs[key] = value;
        }

        return pairs;
    }

    private static string GetRequiredQueryValue(IReadOnlyDictionary<string, string> query, string key)
    {
        if (query.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value))
        {
            return value;
        }

        throw new InvalidOperationException($"Invalid Elvern VLC handoff URL: missing '{key}' query parameter.");
    }
}
