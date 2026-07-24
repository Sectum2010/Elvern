using System.Reflection;

namespace Elvern.VlcOpener.Services;

public static class HelperOriginPolicy
{
    private const string ResourceName = "allowed_origin.txt";

    public static string ResolveAllowedOrigin(string? overrideOrigin = null)
    {
        var candidate = string.IsNullOrWhiteSpace(overrideOrigin)
            ? ReadEmbeddedAllowedOrigin()
            : overrideOrigin;
        var normalized = NormalizeOrigin(candidate);
        if (string.IsNullOrWhiteSpace(normalized))
        {
            throw new InvalidOperationException(
                "Elvern VLC helper was not built with an allowed backend origin.");
        }
        return normalized;
    }

    public static string? NormalizeOrigin(string? value)
    {
        var candidate = (value ?? string.Empty).Trim();
        if (candidate.Length == 0
            || !string.Equals(candidate, value, StringComparison.Ordinal)
            || candidate.Contains('%')
            || candidate.Any(character => character > 127 || character == 127 || char.IsControl(character))
            || !Uri.TryCreate(candidate, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)
            || !string.IsNullOrEmpty(uri.UserInfo)
            || string.IsNullOrEmpty(uri.Host)
            || (uri.AbsolutePath != "/" && !string.IsNullOrEmpty(uri.AbsolutePath))
            || !string.IsNullOrEmpty(uri.Query)
            || !string.IsNullOrEmpty(uri.Fragment))
        {
            return null;
        }
        var scheme = uri.Scheme.ToLowerInvariant();
        var host = uri.Host.Trim('[', ']').ToLowerInvariant();
        if (host.Split('.').Any(label => label.StartsWith("xn--", StringComparison.OrdinalIgnoreCase)))
        {
            return null;
        }
        if (uri.HostNameType == UriHostNameType.IPv6)
        {
            host = $"[{host}]";
        }
        var authority = uri.IsDefaultPort ? host : $"{host}:{uri.Port}";
        return $"{scheme}://{authority}";
    }

    public static bool OriginsMatch(string attemptedOrigin, string allowedOrigin)
    {
        var attempted = NormalizeOrigin(attemptedOrigin);
        var allowed = NormalizeOrigin(allowedOrigin);
        return !string.IsNullOrWhiteSpace(attempted)
            && !string.IsNullOrWhiteSpace(allowed)
            && string.Equals(attempted, allowed, StringComparison.Ordinal);
    }

    private static string? ReadEmbeddedAllowedOrigin()
    {
        var assembly = Assembly.GetExecutingAssembly();
        var metadataOrigin = assembly
            .GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(attribute =>
                string.Equals(attribute.Key, "ElvernAllowedOrigin", StringComparison.OrdinalIgnoreCase))
            ?.Value;
        if (!string.IsNullOrWhiteSpace(metadataOrigin))
        {
            return metadataOrigin.Trim();
        }

        var resourceName = assembly.GetManifestResourceNames()
            .FirstOrDefault(name => name.EndsWith(ResourceName, StringComparison.OrdinalIgnoreCase));
        if (string.IsNullOrWhiteSpace(resourceName))
        {
            return null;
        }
        using var stream = assembly.GetManifestResourceStream(resourceName);
        if (stream is null)
        {
            return null;
        }
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd().Trim();
    }
}
