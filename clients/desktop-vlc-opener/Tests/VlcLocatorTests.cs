using Elvern.VlcOpener.Services;
using System.Reflection;
using Xunit;

namespace Elvern.VlcOpener.Tests;

public sealed class VlcLocatorTests
{
    [Fact]
    public void HelperInformationalVersionMatchesTheAssemblySemanticVersion()
    {
        var assembly = typeof(VlcLocator).Assembly;
        var assemblyVersion = assembly.GetName().Version!;
        var informationalVersion = assembly
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()!
            .InformationalVersion;

        Assert.Equal(
            $"{assemblyVersion.Major}.{assemblyVersion.Minor}.{assemblyVersion.Build}",
            informationalVersion);
        Assert.DoesNotContain("+", informationalVersion);
    }

    [Fact]
    public void LinuxCandidatesPreferEnvironmentThenPathThenStandardLocations()
    {
        var candidates = VlcLocator.BuildLinuxCandidates(
            "/custom/vlc",
            "/home/user/bin:/opt/video/bin");

        Assert.Equal(
            new[]
            {
                "/custom/vlc",
                "/home/user/bin/vlc",
                "/opt/video/bin/vlc",
                "/usr/bin/vlc",
                "/usr/local/bin/vlc",
                "/snap/bin/vlc",
            },
            candidates);
    }

    [Fact]
    public void LinuxCandidatesDoNotDuplicateStandardPathEntries()
    {
        var candidates = VlcLocator.BuildLinuxCandidates(null, "/usr/bin:/snap/bin");

        Assert.Equal(new[] { "/usr/bin/vlc", "/snap/bin/vlc", "/usr/local/bin/vlc" }, candidates);
    }

    [Theory]
    [InlineData("/custom/vlc", "/bin", "/custom/vlc")]
    [InlineData(null, "/home/user/bin:/opt/video/bin", "/home/user/bin/vlc")]
    [InlineData(null, "/empty", "/usr/bin/vlc")]
    [InlineData(null, "/empty", "/usr/local/bin/vlc")]
    [InlineData(null, "/empty", "/snap/bin/vlc")]
    public void LinuxLookupReturnsTheFirstExecutableCandidate(
        string? envOverride,
        string pathValue,
        string expected)
    {
        var result = VlcLocator.FindLinuxVlc(
            envOverride,
            pathValue,
            candidate => candidate == expected);

        Assert.Equal(expected, result);
    }

    [Fact]
    public void LinuxLookupReturnsNullWhenNoCandidateIsExecutable()
    {
        var result = VlcLocator.FindLinuxVlc(null, "/empty", _ => false);

        Assert.Null(result);
    }
}
