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

    public async Task PreflightPlaybackTargetAsync(
        string targetUrl,
        CancellationToken cancellationToken = default)
    {
        if (!Uri.TryCreate(targetUrl, UriKind.Absolute, out var targetUri)
            || (targetUri.Scheme != Uri.UriSchemeHttp && targetUri.Scheme != Uri.UriSchemeHttps))
        {
            throw new InvalidOperationException($"Cannot preflight invalid playback URL target: {targetUrl}");
        }

        var headResult = await TryPreflightRequestAsync(
            () => new HttpRequestMessage(HttpMethod.Head, targetUri),
            "HEAD",
            cancellationToken
        ).ConfigureAwait(false);
        if (headResult.Success)
        {
            return;
        }

        OpenerLog.Info($"URL target HEAD preflight did not pass; trying GET range. Result: {headResult.Summary}");
        var rangeResult = await TryPreflightRequestAsync(
            () =>
            {
                var request = new HttpRequestMessage(HttpMethod.Get, targetUri);
                request.Headers.Range = new RangeHeaderValue(0, 1);
                return request;
            },
            "GET range bytes=0-1",
            cancellationToken
        ).ConfigureAwait(false);
        if (rangeResult.Success)
        {
            return;
        }

        throw new InvalidOperationException(
            $"VLC URL target preflight failed before launch. HEAD: {headResult.Summary}; GET range: {rangeResult.Summary}");
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

    private async Task<PreflightResult> TryPreflightRequestAsync(
        Func<HttpRequestMessage> createRequest,
        string methodLabel,
        CancellationToken cancellationToken)
    {
        try
        {
            using var request = createRequest();
            request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("*/*"));
            using var response = await _httpClient.SendAsync(
                request,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken
            ).ConfigureAwait(false);
            var bodyPreview = response.IsSuccessStatusCode
                ? string.Empty
                : await ReadSmallBodyPreviewAsync(response, cancellationToken).ConfigureAwait(false);
            var finalUrl = response.RequestMessage?.RequestUri?.ToString() ?? request.RequestUri?.ToString() ?? "";
            var contentLength = response.Content.Headers.ContentLength?.ToString() ?? "unknown";
            var contentType = response.Content.Headers.ContentType?.ToString() ?? "unknown";
            var acceptRanges = response.Headers.AcceptRanges.Any()
                ? string.Join(",", response.Headers.AcceptRanges)
                : "unknown";
            var contentRange = response.Content.Headers.ContentRange?.ToString() ?? "none";
            var summary =
                $"{methodLabel} status={(int)response.StatusCode} {response.ReasonPhrase}; "
                + $"finalUrl={finalUrl}; contentType={contentType}; acceptRanges={acceptRanges}; "
                + $"contentLength={contentLength}; contentRange={contentRange}"
                + (string.IsNullOrWhiteSpace(bodyPreview) ? "" : $"; bodyPreview={bodyPreview}");
            OpenerLog.Info($"URL target preflight: {summary}");
            return new PreflightResult(response.IsSuccessStatusCode, summary);
        }
        catch (Exception ex)
        {
            var summary = $"{methodLabel} exception={ex.GetType().Name}: {ex.Message}";
            OpenerLog.Info($"URL target preflight: {summary}");
            return new PreflightResult(false, summary);
        }
    }

    private static async Task<string> ReadSmallBodyPreviewAsync(
        HttpResponseMessage response,
        CancellationToken cancellationToken)
    {
        const int MaxPreviewBytes = 512;
        try
        {
            await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
            var buffer = new byte[MaxPreviewBytes];
            var bytesRead = await stream.ReadAsync(buffer.AsMemory(0, buffer.Length), cancellationToken).ConfigureAwait(false);
            if (bytesRead <= 0)
            {
                return string.Empty;
            }
            return System.Text.Encoding.UTF8.GetString(buffer, 0, bytesRead).ReplaceLineEndings(" ").Trim();
        }
        catch (Exception ex)
        {
            return $"<failed to read response preview: {ex.Message}>";
        }
    }

    private sealed record PreflightResult(bool Success, string Summary);

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
