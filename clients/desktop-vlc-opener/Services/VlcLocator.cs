using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Microsoft.Win32;

namespace Elvern.VlcOpener.Services;

internal sealed record VlcDetectionResult(string Status, string? Path);

internal static class VlcLocator
{
    public static VlcDetectionResult ProbeInstalledVlc()
    {
        try
        {
            return new VlcDetectionResult("installed", FindInstalledVlc());
        }
        catch (InvalidOperationException)
        {
            return new VlcDetectionResult("not_detected", null);
        }
        catch
        {
            return new VlcDetectionResult("detection_unavailable", null);
        }
    }

    public static string FindInstalledVlc()
    {
        var checkedLocations = new List<string>();
        var fromEnv = Environment.GetEnvironmentVariable("ELVERN_VLC_PATH");
        if (!string.IsNullOrWhiteSpace(fromEnv) && IsExecutableFile(fromEnv))
        {
            return fromEnv;
        }
        if (!string.IsNullOrWhiteSpace(fromEnv))
        {
            checkedLocations.Add("ELVERN_VLC_PATH");
        }

        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            var appPath = FindWindowsVlcFromAppPaths();
            if (!string.IsNullOrWhiteSpace(appPath))
            {
                return appPath;
            }

            foreach (var candidate in BuildWindowsCandidates())
            {
                checkedLocations.Add(candidate);
                if (IsExecutableFile(candidate))
                {
                    return candidate;
                }
            }
        }
        else if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
        {
            foreach (var candidate in new[]
                     {
                         "/Applications/VLC.app/Contents/MacOS/VLC",
                         Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Applications", "VLC.app", "Contents", "MacOS", "VLC"),
                     })
            {
                checkedLocations.Add(candidate);
                if (IsExecutableFile(candidate))
                {
                    return candidate;
                }
            }
        }
        else
        {
            var linuxResult = FindLinuxVlc(
                fromEnv,
                Environment.GetEnvironmentVariable("PATH"),
                IsExecutableFile);
            if (!string.IsNullOrWhiteSpace(linuxResult))
            {
                return linuxResult;
            }
            checkedLocations.Add("PATH/vlc");
            checkedLocations.Add("/usr/bin/vlc");
            checkedLocations.Add("/usr/local/bin/vlc");
            checkedLocations.Add("/snap/bin/vlc");
        }

        throw new InvalidOperationException(
            $"Installed VLC was not found. Set ELVERN_VLC_PATH or install VLC first. Checked: {string.Join(", ", checkedLocations)}");
    }

    internal static IReadOnlyList<string> BuildLinuxCandidates(string? envOverride, string? pathValue)
    {
        var candidates = new List<string>();
        var seen = new HashSet<string>(StringComparer.Ordinal);

        void Add(string? candidate)
        {
            if (!string.IsNullOrWhiteSpace(candidate) && seen.Add(candidate))
            {
                candidates.Add(candidate);
            }
        }

        Add(envOverride);
        foreach (var entry in (pathValue ?? string.Empty)
                     .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            Add(Path.Combine(entry, "vlc"));
        }
        Add("/usr/bin/vlc");
        Add("/usr/local/bin/vlc");
        Add("/snap/bin/vlc");
        return candidates;
    }

    internal static string? FindLinuxVlc(
        string? envOverride,
        string? pathValue,
        Func<string, bool> isExecutable)
    {
        foreach (var candidate in BuildLinuxCandidates(envOverride, pathValue))
        {
            if (isExecutable(candidate))
            {
                return candidate;
            }
        }
        return null;
    }

    private static bool IsExecutableFile(string candidate)
    {
        if (!File.Exists(candidate))
        {
            return false;
        }
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            return true;
        }
        try
        {
            var mode = File.GetUnixFileMode(candidate);
            return (mode & (UnixFileMode.UserExecute | UnixFileMode.GroupExecute | UnixFileMode.OtherExecute)) != 0;
        }
        catch (PlatformNotSupportedException)
        {
            return true;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
    }

    [SupportedOSPlatform("windows")]
    private static string? FindWindowsVlcFromAppPaths()
    {
        foreach (var registryHive in new[] { Registry.CurrentUser, Registry.LocalMachine })
        {
            using var key = registryHive.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\App Paths\vlc.exe");
            var value = key?.GetValue(string.Empty) as string;
            if (!string.IsNullOrWhiteSpace(value) && File.Exists(value))
            {
                return value;
            }
        }

        return null;
    }

    private static IEnumerable<string> BuildWindowsCandidates()
    {
        var candidates = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        void AddCandidate(string? path)
        {
            if (!string.IsNullOrWhiteSpace(path))
            {
                candidates.Add(path);
            }
        }

        AddCandidate(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "VideoLAN", "VLC", "vlc.exe"));
        AddCandidate(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "VideoLAN", "VLC", "vlc.exe"));
        AddCandidate(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "VideoLAN", "VLC", "vlc.exe"));
        AddCandidate(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VLC", "vlc.exe"));

        var pathEntries = (Environment.GetEnvironmentVariable("PATH") ?? string.Empty)
            .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        foreach (var entry in pathEntries)
        {
            AddCandidate(Path.Combine(entry, "vlc.exe"));
        }

        return candidates;
    }
}
