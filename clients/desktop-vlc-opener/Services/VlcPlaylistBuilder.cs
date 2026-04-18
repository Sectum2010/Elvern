using System.Security;
using System.Text;
using Elvern.VlcOpener.Models;

namespace Elvern.VlcOpener.Services;

internal static class VlcPlaylistBuilder
{
    public static string CreateTemporaryPlaylist(DesktopVlcHandoff handoff)
    {
        var tempRoot = Path.Combine(Path.GetTempPath(), "ElvernVlcOpener");
        Directory.CreateDirectory(tempRoot);
        CleanupOldPlaylists(tempRoot);

        var safeTitle = BuildSafeFileName(handoff.Title);
        var handoffPrefix = handoff.HandoffId.Length >= 8 ? handoff.HandoffId[..8] : handoff.HandoffId;
        var playlistPath = Path.Combine(tempRoot, $"{safeTitle}-{handoffPrefix}.xspf");
        var contents = BuildXspfPlaylist(handoff);
        File.WriteAllText(playlistPath, contents, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
        OpenerLog.Info($"Created temporary VLC playlist: {playlistPath}");
        return playlistPath;
    }

    private static void CleanupOldPlaylists(string tempRoot)
    {
        try
        {
            var cutoff = DateTime.UtcNow.AddDays(-2);
            foreach (var file in Directory.EnumerateFiles(tempRoot, "*.xspf", SearchOption.TopDirectoryOnly))
            {
                try
                {
                    var timestamp = File.GetLastWriteTimeUtc(file);
                    if (timestamp < cutoff)
                    {
                        File.Delete(file);
                    }
                }
                catch
                {
                    // Ignore cleanup failures for best-effort temp maintenance.
                }
            }
        }
        catch
        {
            // Ignore cleanup failures.
        }
    }

    private static string BuildXspfPlaylist(DesktopVlcHandoff handoff)
    {
        var escapedTitle = XmlEscape(handoff.Title);
        var escapedLocation = XmlEscape(handoff.Target);
        var builder = new StringBuilder();
        builder.AppendLine("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
        builder.AppendLine("<playlist version=\"1\" xmlns=\"http://xspf.org/ns/0/\" xmlns:vlc=\"http://www.videolan.org/vlc/playlist/ns/0/\">");
        builder.AppendLine($"  <title>{escapedTitle}</title>");
        builder.AppendLine("  <trackList>");
        builder.AppendLine("    <track>");
        builder.AppendLine($"      <location>{escapedLocation}</location>");
        builder.AppendLine($"      <title>{escapedTitle}</title>");
        if (handoff.ResumeSeconds > 0)
        {
            builder.AppendLine("      <extension application=\"http://www.videolan.org/vlc/playlist/0\">");
            builder.AppendLine($"        <vlc:option>start-time={XmlEscape($"{handoff.ResumeSeconds:F3}")}</vlc:option>");
            builder.AppendLine("      </extension>");
        }
        builder.AppendLine("    </track>");
        builder.AppendLine("  </trackList>");
        builder.AppendLine("</playlist>");
        return builder.ToString();
    }

    private static string BuildSafeFileName(string title)
    {
        var invalid = Path.GetInvalidFileNameChars().ToHashSet();
        var cleaned = new string(title.Select(character => invalid.Contains(character) ? '_' : character).ToArray()).Trim();
        return string.IsNullOrWhiteSpace(cleaned) ? "Elvern-VLC-Opener" : cleaned;
    }

    private static string XmlEscape(string value)
    {
        return SecurityElement.Escape(value) ?? string.Empty;
    }
}
