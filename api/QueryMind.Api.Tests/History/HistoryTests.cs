using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using QueryMind.Api.Data;

namespace QueryMind.Api.Tests.History;

[Collection(ApiTestGroup.Name)]
public sealed class HistoryTests(PostgresServer server) : IAsyncLifetime
{
    private ApiFactory _factory = null!;
    private HttpClient _client = null!;
    private Caller _admin = null!;
    private Caller _alice = null!;
    private Caller _bob = null!;

    private sealed record Caller(string Id, string Email, string Token);

    public async Task InitializeAsync()
    {
        _factory = await ApiFactory.CreateAsync(server);
        _client = _factory.CreateClient();
        _admin = await Login(ApiFactory.AdminEmail, ApiFactory.AdminPassword);
        _alice = await SignUp("alice@example.com");
        _bob = await SignUp("bob@example.com");
    }

    public Task DisposeAsync()
    {
        _client.Dispose();
        _factory.Dispose();
        return Task.CompletedTask;
    }

    // --- own history (R7.1) ---

    [Fact]
    public async Task UserSeesOnlyTheirOwnHistory_NewestFirst_Paged()
    {
        var aliceIds = new List<string>();
        foreach (var q in new[] { "first?", "second?", "third?" })
        {
            aliceIds.Add(await AskAs(_alice, q));
        }

        await AskAs(_bob, "bob's question?");

        var page1 = await GetJson(_alice, "/api/history?page=1&pageSize=2");
        Assert.Equal((1, 2, 3), (page1.GetProperty("page").GetInt32(), page1.GetProperty("pageSize").GetInt32(), page1.GetProperty("total").GetInt32()));
        var items = page1.GetProperty("items");
        Assert.Equal(["third?", "second?"], items.EnumerateArray().Select(i => i.GetProperty("question").GetString()));
        Assert.Equal(
            ["id", "question", "status", "finalSql", "rowCount", "totalMs", "createdAt"],
            items[0].EnumerateObject().Select(p => p.Name)); // no userEmail for users

        var page2 = await GetJson(_alice, "/api/history?page=2&pageSize=2");
        Assert.Equal(aliceIds[0], Assert.Single(page2.GetProperty("items").EnumerateArray()).GetProperty("id").GetString());

        var bobs = await GetJson(_bob, "/api/history");
        Assert.Equal(1, bobs.GetProperty("total").GetInt32());
        Assert.Equal(20, bobs.GetProperty("pageSize").GetInt32()); // default
    }

    [Fact]
    public async Task AnotherUsersEntry_Is404_ForDetailAndRerun()
    {
        var bobsId = await AskAs(_bob, "bob's question?");
        var queriesBefore = _factory.Engine.Queries.Count;

        using var detail = await Send(_alice, HttpMethod.Get, $"/api/history/{bobsId}");
        using var rerun = await Send(_alice, HttpMethod.Post, $"/api/history/{bobsId}/rerun");
        using var unknown = await Send(_alice, HttpMethod.Get, $"/api/history/{Guid.NewGuid()}");

        Assert.Equal(HttpStatusCode.NotFound, detail.StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, rerun.StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, unknown.StatusCode);
        // The same response whether the entry exists or not.
        Assert.Equal(await ErrorMessage(unknown), await ErrorMessage(detail));
        Assert.Equal(queriesBefore, _factory.Engine.Queries.Count);
    }

    [Theory]
    [InlineData("?page=0")]
    [InlineData("?pageSize=0")]
    [InlineData("?pageSize=101")]
    public async Task BadPaging_Returns400(string query)
    {
        using var response = await Send(_alice, HttpMethod.Get, $"/api/history{query}");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    // --- detail (R3.3, R7.1) ---

    [Fact]
    public async Task Detail_IncludesAttemptsTimingsAndUsage_AndEmailOnlyForAdmins()
    {
        var id = await AskAs(_alice, "with attempts?");

        var own = await GetJson(_alice, $"/api/history/{id}");
        var attempts = own.GetProperty("attempts");
        Assert.Equal(2, attempts.GetArrayLength());
        Assert.Equal([1, 2], attempts.EnumerateArray().Select(a => a.GetProperty("n").GetInt32()));
        Assert.Equal("EXECUTION_ERROR", attempts[0].GetProperty("errorCode").GetString());
        Assert.Equal(2810, own.GetProperty("timings").GetProperty("engineTotalMs").GetInt32());
        Assert.Equal(3700, own.GetProperty("usage").GetProperty("promptTokens").GetInt32());
        Assert.False(own.TryGetProperty("userEmail", out _));

        var asAdmin = await GetJson(_admin, $"/api/history/{id}");
        Assert.Equal("alice@example.com", asAdmin.GetProperty("userEmail").GetString());
    }

    // --- rerun (R7.2) ---

    [Fact]
    public async Task Rerun_CreatesANewRowLinkedToTheOriginal()
    {
        var original = await AskAs(_alice, "how many orders?");

        using var response = await Send(_alice, HttpMethod.Post, $"/api/history/{original}/rerun");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await Json(response);
        var newId = body.GetProperty("historyId").GetString()!;
        Assert.NotEqual(original, newId);
        Assert.Equal("how many orders?", body.GetProperty("question").GetString());
        Assert.Equal("how many orders?", ReadQuestion(_factory.Engine.Queries[^1].Body)); // re-asked, not re-executed

        var row = await Row(Guid.Parse(newId));
        Assert.Equal(Guid.Parse(original), row.RerunOfId);
        Assert.Equal(Guid.Parse(_alice.Id), row.UserId);
        Assert.Equal(2, (await GetJson(_alice, "/api/history")).GetProperty("total").GetInt32());
    }

    [Fact]
    public async Task AdminRerunOfAUsersQuestion_IsOwnedByTheAdmin()
    {
        var original = await AskAs(_alice, "alice's question?");

        using var response = await Send(_admin, HttpMethod.Post, $"/api/history/{original}/rerun");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var row = await Row(Guid.Parse((await Json(response)).GetProperty("historyId").GetString()!));
        Assert.Equal(Guid.Parse(_admin.Id), row.UserId);
        Assert.Equal(Guid.Parse(original), row.RerunOfId);
    }

    // --- admin history (R8.2) ---

    [Fact]
    public async Task AdminSeesAllHistory_WithEmails_AndCanFilterByUser()
    {
        await AskAs(_alice, "a1?");
        await AskAs(_alice, "a2?");
        await AskAs(_bob, "b1?");

        var all = await GetJson(_admin, "/api/admin/history");
        Assert.Equal(3, all.GetProperty("total").GetInt32());
        Assert.Equal(
            ["bob@example.com", "alice@example.com", "alice@example.com"],
            all.GetProperty("items").EnumerateArray().Select(i => i.GetProperty("userEmail").GetString()));

        var bobsOnly = await GetJson(_admin, $"/api/admin/history?userId={_bob.Id}");
        Assert.Equal(1, bobsOnly.GetProperty("total").GetInt32());
        Assert.Equal("b1?", bobsOnly.GetProperty("items")[0].GetProperty("question").GetString());
    }

    [Fact]
    public async Task AdminHistory_IsForbiddenForUsers()
    {
        using var response = await Send(_alice, HttpMethod.Get, "/api/admin/history");

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    // --- schema (R1.3, R1.2) ---

    [Fact]
    public async Task Schema_IsProxiedForAnyUser_InCamelCase()
    {
        using var response = await Send(_alice, HttpMethod.Get, "/api/schema");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await Json(response);
        Assert.Equal("hash-1", body.GetProperty("schemaHash").GetString());
        var orders = body.GetProperty("tables")[0];
        Assert.True(orders.GetProperty("columns")[0].GetProperty("primaryKey").GetBoolean());
        Assert.Equal(["10248", "10249"], orders.GetProperty("columns")[0].GetProperty("samples").EnumerateArray().Select(s => s.GetString()));
        Assert.Equal("customers", orders.GetProperty("foreignKeys")[0].GetProperty("refTable").GetString());

        var call = _factory.Engine.Requests.Last(r => r.Path == "/v1/schema");
        Assert.Equal(ApiFactory.EngineKey, call.Headers["X-Internal-Key"]);
    }

    [Fact]
    public async Task Schema_RequiresSignIn()
    {
        using var anonymous = _factory.CreateClient();

        using var response = await anonymous.GetAsync(new Uri("/api/schema", UriKind.Relative));

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Schema_EngineNotReady_Returns503()
    {
        _factory.Engine.OnSchema = () => FakeEngine.Json(HttpStatusCode.ServiceUnavailable,
            """{"error":{"code":"TARGET_DB_UNAVAILABLE","message":"The schema has not been loaded yet."}}""");

        using var response = await Send(_alice, HttpMethod.Get, "/api/schema");

        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.Equal("TARGET_DB_UNAVAILABLE", (await Json(response)).GetProperty("error").GetProperty("code").GetString());
    }

    [Fact]
    public async Task SchemaRefresh_Is403ForUsers_And200ForAdmins()
    {
        using var asUser = await Send(_alice, HttpMethod.Post, "/api/admin/schema/refresh");
        Assert.Equal(HttpStatusCode.Forbidden, asUser.StatusCode);
        Assert.DoesNotContain(_factory.Engine.Requests, r => r.Path == "/v1/schema/refresh");

        using var asAdmin = await Send(_admin, HttpMethod.Post, "/api/admin/schema/refresh");

        Assert.Equal(HttpStatusCode.OK, asAdmin.StatusCode);
        var body = await Json(asAdmin);
        Assert.Equal(["tables", "schemaHash", "introspectedAt"], body.EnumerateObject().Select(p => p.Name));
        Assert.Equal(2, body.GetProperty("tables").GetInt32());
        Assert.Equal("hash-2", body.GetProperty("schemaHash").GetString());
        Assert.Single(_factory.Engine.Requests, r => r.Path == "/v1/schema/refresh");
    }

    // --- helpers ---

    private async Task<Caller> SignUp(string email)
    {
        using var register = await _client.PostAsJsonAsync("/api/auth/register", new { email, password = "correct horse battery" });
        Assert.Equal(HttpStatusCode.Created, register.StatusCode);
        return await Login(email, "correct horse battery");
    }

    private async Task<Caller> Login(string email, string password)
    {
        using var login = await _client.PostAsJsonAsync("/api/auth/login", new { email, password });
        var body = await Json(login);
        return new Caller(body.GetProperty("user").GetProperty("id").GetString()!, email, body.GetProperty("accessToken").GetString()!);
    }

    private async Task<string> AskAs(Caller caller, string question)
    {
        using var response = await Send(caller, HttpMethod.Post, "/api/ask", new { question });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return (await Json(response)).GetProperty("historyId").GetString()!;
    }

    private async Task<HttpResponseMessage> Send(Caller caller, HttpMethod method, string path, object? body = null)
    {
        using var request = new HttpRequestMessage(method, path);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", caller.Token);
        if (body is not null)
        {
            request.Content = JsonContent.Create(body);
        }

        return await _client.SendAsync(request);
    }

    private async Task<JsonElement> GetJson(Caller caller, string path)
    {
        using var response = await Send(caller, HttpMethod.Get, path);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return await Json(response);
    }

    private async Task<QueryHistory> Row(Guid id)
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        return await scope.ServiceProvider.GetRequiredService<AppDbContext>().QueryHistory.AsNoTracking().SingleAsync(h => h.Id == id);
    }

    private static string? ReadQuestion(string body)
    {
        using var doc = JsonDocument.Parse(body);
        return doc.RootElement.GetProperty("question").GetString();
    }

    private static async Task<JsonElement> Json(HttpResponseMessage response) =>
        JsonDocument.Parse(await response.Content.ReadAsStringAsync()).RootElement.Clone();

    private static async Task<string?> ErrorMessage(HttpResponseMessage response) =>
        (await Json(response)).GetProperty("error").GetProperty("message").GetString();
}
