using System.Net.Http.Headers;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using Elvern.VlcOpener.Models;

namespace Elvern.VlcOpener.Services;

internal sealed class ElvernDesktopApiClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly HttpClient _httpClient = new();

    public async Task<DesktopVlcHandoff> ResolveHandoffAsync(
        string resolveUrl,
        VlcDetectionResult? vlcDetection = null,
        CancellationToken cancellationToken = default)
    {
        var helperVersion = ResolveHelperVersion();
        var helperPlatform = ResolveHelperPlatform();
        var helperArch = RuntimeInformation.OSArchitecture.ToString().ToLowerInvariant();

        using var request = new HttpRequestMessage(HttpMethod.Get, resolveUrl);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        request.Headers.TryAddWithoutValidation("X-Elvern-Helper-Version", helperVersion);
        request.Headers.TryAddWithoutValidation("X-Elvern-Helper-Platform", helperPlatform);
        request.Headers.TryAddWithoutValidation("X-Elvern-Helper-Arch", helperArch);
        if (vlcDetection is not null)
        {
            request.Headers.TryAddWithoutValidation("X-Elvern-VLC-Detection-State", vlcDetection.Status);
            if (!string.IsNullOrWhiteSpace(vlcDetection.Path))
            {
                request.Headers.TryAddWithoutValidation("X-Elvern-VLC-Detection-Path", vlcDetection.Path);
            }
        }
        OpenerLog.Info(
            $"Desktop helper metadata headers: version={helperVersion} platform={helperPlatform} arch={helperArch} vlcDetection={vlcDetection?.Status ?? "unknown"}");
        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        var payload = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        OpenerLog.Info($"Desktop helper resolve URL: {resolveUrl}");
        OpenerLog.Info($"Desktop helper raw JSON: {payload}");

        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException($"Failed to resolve desktop VLC handoff ({(int)response.StatusCode}): {ExtractErrorMessage(payload)}");
        }

        var handoff = JsonSerializer.Deserialize<DesktopVlcHandoff>(payload, JsonOptions)
            ?? throw new InvalidOperationException("Desktop VLC handoff response was empty.");

        if (string.IsNullOrWhiteSpace(handoff.Target))
        {
            throw new InvalidOperationException("Desktop VLC handoff did not include a playback target.");
        }

        return handoff;
    }

    public async Task VerifyVlcAsync(
        string verifyUrl,
        VlcDetectionResult? vlcDetection = null,
        CancellationToken cancellationToken = default)
    {
        var helperVersion = ResolveHelperVersion();
        var helperPlatform = ResolveHelperPlatform();
        var helperArch = RuntimeInformation.OSArchitecture.ToString().ToLowerInvariant();

        using var request = new HttpRequestMessage(HttpMethod.Get, verifyUrl);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        request.Headers.TryAddWithoutValidation("X-Elvern-Helper-Version", helperVersion);
        request.Headers.TryAddWithoutValidation("X-Elvern-Helper-Platform", helperPlatform);
        request.Headers.TryAddWithoutValidation("X-Elvern-Helper-Arch", helperArch);
        if (vlcDetection is not null)
        {
            request.Headers.TryAddWithoutValidation("X-Elvern-VLC-Detection-State", vlcDetection.Status);
            if (!string.IsNullOrWhiteSpace(vlcDetection.Path))
            {
                request.Headers.TryAddWithoutValidation("X-Elvern-VLC-Detection-Path", vlcDetection.Path);
            }
        }

        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        var payload = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        OpenerLog.Info($"Desktop helper verify URL: {verifyUrl}");
        OpenerLog.Info($"Desktop helper verify response: {payload}");

        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException($"Failed to report desktop VLC verification ({(int)response.StatusCode}): {ExtractErrorMessage(payload)}");
        }
    }

    public async Task ReportLaunchStartedAsync(
        string startedUrl,
        CancellationToken cancellationToken = default)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, startedUrl);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        using var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
        var payload = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        OpenerLog.Info($"Desktop helper started callback URL: {startedUrl}");
        OpenerLog.Info($"Desktop helper started callback response: {payload}");

        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException($"Failed to report desktop VLC launch ({(int)response.StatusCode}): {ExtractErrorMessage(payload)}");
        }
    }

    public void Dispose()
    {
        _httpClient.Dispose();
    }

    private static string ExtractErrorMessage(string payload)
    {
        try
        {
            using var document = JsonDocument.Parse(payload);
            if (document.RootElement.ValueKind == JsonValueKind.Object
                && document.RootElement.TryGetProperty("detail", out var detail)
                && detail.ValueKind == JsonValueKind.String)
            {
                return detail.GetString() ?? "Request failed";
            }
        }
        catch (JsonException)
        {
            // Ignore and fall back to raw payload.
        }

        return string.IsNullOrWhiteSpace(payload) ? "Request failed" : payload.Trim();
    }

    private static string ResolveHelperVersion()
    {
        var assembly = Assembly.GetEntryAssembly() ?? Assembly.GetExecutingAssembly();
        var informationalVersion = assembly
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?
            .InformationalVersion;
        if (!string.IsNullOrWhiteSpace(informationalVersion))
        {
            return informationalVersion;
        }

        var version = assembly.GetName().Version;
        return version is null ? "0.0.0" : version.ToString();
    }

    private static string ResolveHelperPlatform()
    {
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            return "windows";
        }
        if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
        {
            return "mac";
        }
        return "linux";
    }
}
