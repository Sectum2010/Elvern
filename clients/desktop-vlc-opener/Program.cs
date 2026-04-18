using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using Elvern.VlcOpener.Models;
using Elvern.VlcOpener.Services;

namespace Elvern.VlcOpener;

internal static class Program
{
    public static async Task<int> Main(string[] args)
    {
        LogLaunchArguments(args);

        if (args.Any(static argument => argument is "--version" or "-v"))
        {
            Console.WriteLine(ResolveHelperVersion());
            return 0;
        }

        var launchArgument = ExtractLaunchArgument(args);
        if (launchArgument is null)
        {
            Console.WriteLine("Usage: dotnet run --project clients/desktop-vlc-opener -- \"elvern-vlc://play?api=...&handoff=...&token=...\" or \"elvern-vlc://verify?api=...&verification=...&token=...\"");
            return args.Length == 0 ? 1 : 0;
        }

        try
        {
            OpenerLog.Info($"Received launch argument: {launchArgument}");
            var context = LaunchUrlParser.Parse(launchArgument);
            OpenerLog.Info($"Parsed helper URI: action={context.Action} scheme={context.Scheme} apiOrigin={context.ApiOrigin}");
            var vlcDetection = VlcLocator.ProbeInstalledVlc();
            OpenerLog.Info($"Local VLC detection result: status={vlcDetection.Status} path={vlcDetection.Path ?? "(none)"}");

            using var apiClient = new ElvernDesktopApiClient();
            if (string.Equals(context.Action, "verify", StringComparison.OrdinalIgnoreCase))
            {
                await apiClient.VerifyVlcAsync(context.VerifyUrl, vlcDetection).ConfigureAwait(false);
                Console.WriteLine("Verified local VLC detection and reported the result back to Elvern.");
                return 0;
            }
            DesktopVlcHandoff handoff = await apiClient.ResolveHandoffAsync(context.ResolveUrl, vlcDetection).ConfigureAwait(false);
            OpenerLog.Info(
                $"Resolved handoff success: title={handoff.Title} strategy={handoff.Strategy} targetKind={handoff.TargetKind} target={handoff.Target}");

            var vlcPath = !string.IsNullOrWhiteSpace(vlcDetection.Path)
                ? vlcDetection.Path
                : VlcLocator.FindInstalledVlc();
            OpenerLog.Info($"Resolved VLC path: {vlcPath}");
            var validatedTarget = ValidateTarget(handoff);
            var launchTarget = PrepareLaunchTarget(validatedTarget);
            var processStart = BuildProcessStartInfo(vlcPath, handoff, launchTarget);
            OpenerLog.Info($"Launching VLC with final target: {launchTarget}");

            var launchedProcess = Process.Start(processStart)
                ?? throw new InvalidOperationException("Failed to start the VLC process.");
            GC.KeepAlive(launchedProcess);
            if (!string.IsNullOrWhiteSpace(handoff.StartedUrl) &&
                string.Equals(handoff.TargetKind, "path", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    await apiClient.ReportLaunchStartedAsync(handoff.StartedUrl).ConfigureAwait(false);
                }
                catch (Exception callbackEx)
                {
                    OpenerLog.Info($"Desktop helper started callback failed after VLC launch: {callbackEx.Message}");
                }
            }

            Console.WriteLine($"Opened \"{handoff.Title}\" in VLC.");
            return 0;
        }
        catch (Exception ex)
        {
            OpenerLog.Error(ex.ToString());
            Console.Error.WriteLine(ex.Message);
            return 1;
        }
    }

    private static void LogLaunchArguments(string[] args)
    {
        OpenerLog.Info($"OS description: {RuntimeInformation.OSDescription}");
        OpenerLog.Info($"Argument count: {args.Length}");
        for (var index = 0; index < args.Length; index += 1)
        {
            OpenerLog.Info($"arg[{index}]={args[index]}");
        }
    }

    private static string? ExtractLaunchArgument(string[] args)
    {
        if (args.Length == 0)
        {
            return null;
        }

        foreach (var argument in args)
        {
            if (argument is "--help" or "-h")
            {
                return null;
            }
        }

        foreach (var argument in args)
        {
            if (LooksLikeProtocolArgument(argument))
            {
                return argument;
            }
        }

        if (args.Length == 1)
        {
            return args[0];
        }

        return null;
    }

    private static bool LooksLikeProtocolArgument(string argument)
    {
        var trimmed = (argument ?? string.Empty).Trim().Trim('"');
        if (trimmed.StartsWith("URL:", StringComparison.OrdinalIgnoreCase))
        {
            trimmed = trimmed[4..].Trim();
        }

        return trimmed.StartsWith("elvern-vlc:", StringComparison.OrdinalIgnoreCase);
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
        return assembly.GetName().Version?.ToString() ?? "0.0.0";
    }

    private static string ValidateTarget(DesktopVlcHandoff handoff)
    {
        if (string.Equals(handoff.TargetKind, "url", StringComparison.OrdinalIgnoreCase))
        {
            if (!Uri.TryCreate(handoff.Target, UriKind.Absolute, out var uri)
                || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
            {
                throw new InvalidOperationException($"Desktop VLC handoff returned an invalid URL target: {handoff.Target}");
            }
            return handoff.Target;
        }

        if (string.Equals(handoff.TargetKind, "path", StringComparison.OrdinalIgnoreCase))
        {
            if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                if (Path.IsPathRooted(handoff.Target) || handoff.Target.StartsWith(@"\\", StringComparison.Ordinal))
                {
                    return handoff.Target;
                }
            }
            else if (Path.IsPathRooted(handoff.Target))
            {
                return handoff.Target;
            }

            throw new InvalidOperationException($"Desktop VLC handoff returned an invalid filesystem path target: {handoff.Target}");
        }

        throw new InvalidOperationException($"Desktop VLC handoff returned an unsupported target kind: {handoff.TargetKind}");
    }

    private static string PrepareLaunchTarget(string validatedTarget)
    {
        return validatedTarget;
    }

    private static ProcessStartInfo BuildProcessStartInfo(string vlcPath, DesktopVlcHandoff handoff, string launchTarget)
    {
        var directStart = new ProcessStartInfo(vlcPath)
        {
            UseShellExecute = false,
        };
        ApplyFreshHandoffFlags(directStart);
        if (handoff.ResumeSeconds > 0)
        {
            directStart.ArgumentList.Add($"--start-time={handoff.ResumeSeconds:F3}");
        }
        directStart.ArgumentList.Add(launchTarget);
        return directStart;
    }

    private static void ApplyFreshHandoffFlags(ProcessStartInfo processStart)
    {
        processStart.ArgumentList.Add("--no-one-instance");
        processStart.ArgumentList.Add("--no-one-instance-when-started-from-file");
        processStart.ArgumentList.Add("--no-playlist-enqueue");
    }
}
