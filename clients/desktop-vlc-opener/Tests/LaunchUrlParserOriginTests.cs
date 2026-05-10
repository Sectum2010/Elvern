using Elvern.VlcOpener.Services;
using Xunit;

namespace Elvern.VlcOpener.Tests;

public sealed class LaunchUrlParserOriginTests
{
    private const string AllowedOrigin = "https://elvern.example.com";

    [Fact]
    public void Parser_AcceptsMatchingOrigin()
    {
        var context = LaunchUrlParser.Parse(
            "elvern-vlc://play?api=https%3A%2F%2Felvern.example.com&handoff=handoff-1&token=token-1",
            AllowedOrigin);

        Assert.Equal(AllowedOrigin, context.ApiOrigin);
    }

    [Fact]
    public void Parser_RejectsForeignOrigin()
    {
        Assert.Throws<InvalidOperationException>(() =>
            LaunchUrlParser.Parse(
                "elvern-vlc://play?api=https%3A%2F%2Fevil.example.com&handoff=handoff-1&token=token-1",
                AllowedOrigin));
    }

    [Fact]
    public void Parser_RejectsHttpWhenAllowedIsHttps()
    {
        Assert.Throws<InvalidOperationException>(() =>
            LaunchUrlParser.Parse(
                "elvern-vlc://play?api=http%3A%2F%2Felvern.example.com&handoff=handoff-1&token=token-1",
                AllowedOrigin));
    }

    [Fact]
    public void Parser_RejectsDifferentPort()
    {
        Assert.Throws<InvalidOperationException>(() =>
            LaunchUrlParser.Parse(
                "elvern-vlc://play?api=https%3A%2F%2Felvern.example.com%3A8443&handoff=handoff-1&token=token-1",
                AllowedOrigin));
    }

    [Fact]
    public void Parser_RejectsSubdomainAttack()
    {
        Assert.Throws<InvalidOperationException>(() =>
            LaunchUrlParser.Parse(
                "elvern-vlc://play?api=https%3A%2F%2Felvern.example.com.evil.com&handoff=handoff-1&token=token-1",
                AllowedOrigin));
    }

    [Fact]
    public void Parser_DefaultsToAllowedOriginWhenApiParamMissing()
    {
        var context = LaunchUrlParser.Parse(
            "elvern-vlc://play?handoff=handoff-1&token=token-1",
            AllowedOrigin);

        Assert.Equal(AllowedOrigin, context.ApiOrigin);
    }
}
