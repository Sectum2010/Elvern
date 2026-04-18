using System.Runtime.InteropServices;

namespace Elvern.VlcOpener.Services;

internal static class OpenerLog
{
    private static readonly object Sync = new();
    private static readonly string LogFilePath = BuildLogFilePath();

    public static void Info(string message) => Write("INFO", message);

    public static void Error(string message) => Write("ERROR", message);

    private static void Write(string level, string message)
    {
        var line = $"[{DateTimeOffset.UtcNow:O}] [{level}] {message}";
        lock (Sync)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogFilePath)!);
            File.AppendAllText(LogFilePath, line + Environment.NewLine);
        }
    }

    private static string BuildLogFilePath()
    {
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            var root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(root, "ElvernVlcOpener", "opener.log");
        }

        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var stateRoot = RuntimeInformation.IsOSPlatform(OSPlatform.OSX)
            ? Path.Combine(home, "Library", "Logs", "ElvernVlcOpener")
            : Path.Combine(home, ".local", "state", "elvern-vlc-opener");
        return Path.Combine(stateRoot, "opener.log");
    }
}
