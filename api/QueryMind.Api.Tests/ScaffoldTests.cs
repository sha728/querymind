using System.Net;
using System.Text.Json;

namespace QueryMind.Api.Tests;

[Collection(ApiTestGroup.Name)]
public sealed class ScaffoldTests(PostgresServer server) : IAsyncLifetime
{
    private const string Header = "X-Correlation-ID";
    private ApiFactory _factory = null!;

    public async Task InitializeAsync() => _factory = await ApiFactory.CreateAsync(server);

    public Task DisposeAsync()
    {
        _factory.Dispose();
        return Task.CompletedTask;
    }

    // --- /health (R10.3) ---

    [Fact]
    public async Task Health_IsAnonymousAndChecksTheAppDb()
    {
        using var client = _factory.CreateClient();

        using var response = await client.GetAsync(new Uri("/health", UriKind.Relative));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("application/json", response.Content.Headers.ContentType?.MediaType);
        using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        Assert.Equal("Healthy", body.RootElement.GetProperty("status").GetString());
        Assert.Equal("Healthy", body.RootElement.GetProperty("checks").GetProperty("app_db").GetString());
    }

    // --- correlation ID (R10.1, design §11.1) ---

    [Fact]
    public async Task ValidCorrelationId_IsEchoed()
    {
        using var client = _factory.CreateClient();
        const string id = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b";

        using var response = await SendWithHeader(client, id);

        Assert.Equal(id, Assert.Single(response.Headers.GetValues(Header)));
    }

    [Fact]
    public async Task ValidCorrelationId_IsNormalisedToLowerCase()
    {
        using var client = _factory.CreateClient();

        using var response = await SendWithHeader(client, "6F1C2A3B-4D5E-4F60-8A7B-9C0D1E2F3A4B");

        Assert.Equal("6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b", Assert.Single(response.Headers.GetValues(Header)));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("not-a-uuid")]
    [InlineData("6f1c2a3b4d5e4f608a7b9c0d1e2f3a4b")] // UUID without hyphens: not the "D" form
    [InlineData("{6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b}")]
    [InlineData("6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b; DROP TABLE users")]
    public async Task MissingOrInvalidCorrelationId_IsReplacedWithNewUuid(string? incoming)
    {
        using var client = _factory.CreateClient();

        using var response = await SendWithHeader(client, incoming);

        var echoed = Assert.Single(response.Headers.GetValues(Header));
        Assert.NotEqual(incoming, echoed);
        Assert.True(Guid.TryParseExact(echoed, "D", out var id));
        Assert.Equal(4, id.Version);
    }

    [Fact]
    public async Task EachRequestWithoutAnId_GetsADifferentOne()
    {
        using var client = _factory.CreateClient();

        using var first = await SendWithHeader(client, null);
        using var second = await SendWithHeader(client, null);

        Assert.NotEqual(first.Headers.GetValues(Header).Single(), second.Headers.GetValues(Header).Single());
    }

    // --- JSON logs (R10.2, design §11.2) ---

    [Fact]
    public async Task LogLines_AreJsonWithCommonFieldsAndCorrelationId()
    {
        using var client = _factory.CreateClient();
        const string id = "0b8e7c6d-5a4f-4e3d-9c2b-1a0f9e8d7c6b";

        using var response = await SendWithHeader(client, id);

        var lines = _factory.LogLines;
        Assert.NotEmpty(lines);
        var parsed = lines.Select(line => JsonDocument.Parse(line)).ToList(); // every line is JSON
        try
        {
            var requestLine = Assert.Single(
                parsed,
                doc => doc.RootElement.TryGetProperty("correlation_id", out var cid) && cid.GetString() == id
                    && doc.RootElement.TryGetProperty("RequestPath", out _));
            var root = requestLine.RootElement;
            Assert.Equal("api", root.GetProperty("service").GetString());
            Assert.Equal("info", root.GetProperty("level").GetString());
            Assert.Contains("/health", root.GetProperty("event").GetString(), StringComparison.Ordinal);
            Assert.True(DateTime.TryParse(root.GetProperty("ts").GetString(), out _));
        }
        finally
        {
            parsed.ForEach(doc => doc.Dispose());
        }
    }

    private static async Task<HttpResponseMessage> SendWithHeader(HttpClient client, string? correlationId)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri("/health", UriKind.Relative));
        if (correlationId is not null)
        {
            request.Headers.TryAddWithoutValidation(Header, correlationId);
        }

        return await client.SendAsync(request);
    }
}
