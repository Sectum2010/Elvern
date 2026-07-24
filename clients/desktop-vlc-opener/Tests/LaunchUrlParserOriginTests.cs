using Elvern.VlcOpener.Services;
using System.Text.Json;
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

    [Theory]
    [InlineData("https://user@elvern.example.com")]
    [InlineData("https://elvern.example.com/path")]
    [InlineData("https://elvern.example.com?query=1")]
    [InlineData("https://elvern.example.com/#fragment")]
    public void OriginPolicy_RejectsValuesThatAreNotExactOrigins(string value)
    {
        Assert.Null(HelperOriginPolicy.NormalizeOrigin(value));
    }

    [Theory]
    [InlineData("HTTPS://ELVERN.EXAMPLE.COM:443/", "https://elvern.example.com")]
    [InlineData("http://ELVERN.EXAMPLE.COM:80", "http://elvern.example.com")]
    [InlineData("https://elvern.example.com:8443", "https://elvern.example.com:8443")]
    public void OriginPolicy_CanonicalizesAuthority(string value, string expected)
    {
        Assert.Equal(expected, HelperOriginPolicy.NormalizeOrigin(value));
    }

    [Fact]
    public void OriginPolicy_MatchesSharedNormalizationMatrix()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "origin-normalization-cases.json");
        var cases = JsonSerializer.Deserialize<List<OriginCase>>(File.ReadAllText(path))
            ?? throw new InvalidOperationException("Origin normalization matrix is unavailable.");

        foreach (var item in cases)
        {
            Assert.Equal(item.Normalized, HelperOriginPolicy.NormalizeOrigin(item.Input));
        }
    }

    private sealed record OriginCase(
        [property: System.Text.Json.Serialization.JsonPropertyName("input")] string Input,
        [property: System.Text.Json.Serialization.JsonPropertyName("normalized")] string? Normalized);
}
