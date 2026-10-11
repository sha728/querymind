using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using QueryMind.Api.Data;

namespace QueryMind.Api.Tests.Ask;

[Collection(ApiTestGroup.Name)]
public sealed class AskTests(PostgresServer server) : IAsyncLifetime
{
    private const string Question = "Which 2 customers paid the most freight?";
    private ApiFactory _factory = null!;
    private HttpClient _client = null!;
    private string _userId = null!;

    public async Task InitializeAsync()
    {
        _factory = await ApiFactory.CreateAsync(server);
        _client = _factory.CreateClient();
        (_userId, var token) = await SignUp("asker@example.com");
        _client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
    }

    public Task DisposeAsync()
    {
        _client.Dispose();
        _factory.Dispose();
        return Task.CompletedTask;
    }

    // --- success: design §4.2 shape and the stored rows (R7.1, R10.4, R10.5) ---

    [Fact]
    public async Task Success_Returns200WithDesignShape()
    {
        _factory.Engine.OnQuery = () =>
        {
            Thread.Sleep(60); // so the .NET total is measurably its own
            return FakeEngine.Json(HttpStatusCode.OK, FakeEngine.SuccessBody);
        };

        using var response = await Ask(Question, correlationId: "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await Json(response);
        Assert.Equal(
            ["historyId", "correlationId", "status", "question", "sql", "columns", "rows", "rowCount", "truncated",
             "chart", "summary", "message", "attempts", "timings", "usage"],
            body.EnumerateObject().Select(p => p.Name));
        Assert.Equal("6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b", body.GetProperty("correlationId").GetString());
        Assert.Equal("success", body.GetProperty("status").GetString());
        Assert.Equal(Question, body.GetProperty("question").GetString());
        Assert.Equal("varchar", body.GetProperty("columns")[0].GetProperty("dbType").GetString());
        Assert.Equal("Ernst Handel", body.GetProperty("rows")[0][0].GetString());
        Assert.Equal(12345.67, body.GetProperty("rows")[0][1].GetDouble());
        Assert.Equal(2, body.GetProperty("rowCount").GetInt32());
        Assert.Equal("pie", body.GetProperty("chart").GetProperty("recommended").GetString());
        Assert.Equal(["pie", "bar", "table"], body.GetProperty("chart").GetProperty("allowed").EnumerateArray().Select(a => a.GetString()));
        Assert.Equal("Ernst Handel spent the most.", body.GetProperty("summary").GetString());
        Assert.Equal(JsonValueKind.Null, body.GetProperty("message").ValueKind);

        var attempts = body.GetProperty("attempts");
        Assert.Equal(2, attempts.GetArrayLength());
        Assert.Equal(["n", "sql", "errorCode", "error", "stage", "latencyMs"], attempts[0].EnumerateObject().Select(p => p.Name));
        Assert.Equal("EXECUTION_ERROR", attempts[0].GetProperty("errorCode").GetString());

        var timings = body.GetProperty("timings");
        Assert.Equal(2810, timings.GetProperty("engineTotalMs").GetInt32());
        var totalMs = timings.GetProperty("totalMs").GetInt32();
        Assert.InRange(totalMs, 60, 2809); // measured by .NET, not copied from the engine
        Assert.Equal(3700, body.GetProperty("usage").GetProperty("promptTokens").GetInt32());
    }

    [Fact]
    public async Task Success_StoresHistoryAndAttemptsWithTimingsTokensAndCorrelationId()
    {
        using var response = await Ask(Question, correlationId: "0b8e7c6d-5a4f-4e3d-9c2b-1a0f9e8d7c6b");
        var body = await Json(response);
        var historyId = Guid.Parse(body.GetProperty("historyId").GetString()!);

        var history = await LoadHistory(historyId);
        Assert.Equal(Guid.Parse(_userId), history.UserId);
        Assert.Equal(Question, history.Question);
        Assert.Equal("success", history.Status);
        Assert.StartsWith("SELECT company_name", history.FinalSql, StringComparison.Ordinal);
        Assert.Equal(2, history.RowCount);
        Assert.False(history.Truncated);
        Assert.Equal("pie", history.ChartType);
        Assert.Equal((35, 1600, 3, 92, 640, 2810), (history.LinkingMs, history.GenerationMs, history.ValidationMs,
            history.ExecutionMs, history.SummaryMs, history.EngineTotalMs));
        Assert.Equal(body.GetProperty("timings").GetProperty("totalMs").GetInt32(), history.TotalMs);
        Assert.Equal((3700, 130, "gpt-oss-120b"), (history.PromptTokens, history.CompletionTokens, history.Model));
        Assert.Equal("0b8e7c6d-5a4f-4e3d-9c2b-1a0f9e8d7c6b", history.CorrelationId);

        var attempts = history.Attempts.OrderBy(a => a.AttemptNo).ToList();
        Assert.Equal([1, 2], attempts.Select(a => (int)a.AttemptNo));
        Assert.Equal(("EXECUTION_ERROR", 700, 1800, 60), (attempts[0].ErrorCode, attempts[0].LatencyMs, attempts[0].PromptTokens, attempts[0].CompletionTokens));
        Assert.Equal("execute", attempts[1].Stage);
    }

    // --- the other statuses are 200 too (design §4.1: domain outcomes are not HTTP errors) ---

    [Theory]
    [InlineData("failed", "SELECT nope FROM orders", "column \\\"nope\\\" does not exist", "EXECUTION_ERROR", "execute")]
    [InlineData("blocked", "SELECT pg_sleep(10)", "The generated query was blocked by the safety validator (FORBIDDEN_FUNCTION: pg_sleep)", "FORBIDDEN_FUNCTION", "validate")]
    [InlineData("cannot_answer", null, "This can't be answered from this database: no salary data", "CANNOT_ANSWER", "extract")]
    public async Task OtherStatuses_Return200WithNullResultFieldsAndAreStored(
        string status, string? sql, string message, string errorCode, string stage)
    {
        _factory.Engine.OnQuery = () => FakeEngine.Json(HttpStatusCode.OK, FakeEngine.NonSuccessBody(status, sql, message, errorCode, stage));

        using var response = await Ask(Question);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await Json(response);
        Assert.Equal(status, body.GetProperty("status").GetString());
        Assert.Equal(sql, body.GetProperty("sql").GetString());
        Assert.Equal(message.Replace("\\\"", "\"", StringComparison.Ordinal), body.GetProperty("message").GetString());
        foreach (var field in new[] { "columns", "rows", "rowCount", "truncated", "chart", "summary" })
        {
            Assert.Equal(JsonValueKind.Null, body.GetProperty(field).ValueKind);
        }

        var history = await LoadHistory(Guid.Parse(body.GetProperty("historyId").GetString()!));
        Assert.Equal(status, history.Status);
        Assert.Equal(sql, history.FinalSql);
        Assert.Null(history.RowCount);
        Assert.Equal(520, history.EngineTotalMs);
        Assert.Equal(errorCode, Assert.Single(history.Attempts).ErrorCode);
    }

    // --- what the engine receives (design §4.3, R10.1) ---

    [Fact]
    public async Task Engine_IsCalledWithInternalKeyCorrelationIdAndOnlyTheQuestion()
    {
        using var response = await Ask($"  {Question}  ", correlationId: "11111111-2222-4333-8444-555555555555");

        var call = Assert.Single(_factory.Engine.Queries);
        Assert.Equal(ApiFactory.EngineKey, call.Headers["X-Internal-Key"]);
        Assert.Equal("11111111-2222-4333-8444-555555555555", call.Headers["X-Correlation-ID"]);
        Assert.Equal(_userId, call.Headers["X-User-Id"]);
        Assert.Equal("user", call.Headers["X-User-Role"]);
        using var sent = JsonDocument.Parse(call.Body);
        Assert.Equal([("question", Question)], sent.RootElement.EnumerateObject().Select(p => (p.Name, p.Value.GetString())));
    }

    // --- infrastructure failures (design §12) are recorded with status=error ---

    [Fact]
    public async Task EngineDown_Returns502AndStoresAnErrorRow()
    {
        _factory.Engine.Down = true;

        using var response = await Ask(Question, correlationId: "22222222-2222-4333-8444-555555555555");

        Assert.Equal(HttpStatusCode.BadGateway, response.StatusCode);
        var error = (await Json(response)).GetProperty("error");
        Assert.Equal("ENGINE_UNAVAILABLE", error.GetProperty("code").GetString());
        Assert.Equal("22222222-2222-4333-8444-555555555555", error.GetProperty("correlationId").GetString());
        Assert.DoesNotContain("engine:8000", error.GetProperty("message").GetString(), StringComparison.Ordinal); // no internals

        var row = await SingleHistoryFor("22222222-2222-4333-8444-555555555555");
        Assert.Equal("error", row.Status);
        Assert.StartsWith("ENGINE_UNAVAILABLE", row.Message, StringComparison.Ordinal);
        Assert.NotNull(row.TotalMs);
        Assert.Empty(row.Attempts);
    }

    [Fact]
    public async Task EngineTimeout_Returns504AndStoresAnErrorRow()
    {
        _factory.Engine.OnQuery = () => throw new TaskCanceledException("timed out", new TimeoutException());

        using var response = await Ask(Question, correlationId: "33333333-2222-4333-8444-555555555555");

        Assert.Equal(HttpStatusCode.GatewayTimeout, response.StatusCode);
        Assert.Equal("ENGINE_TIMEOUT", await ErrorCode(response));
        Assert.Equal("error", (await SingleHistoryFor("33333333-2222-4333-8444-555555555555")).Status);
    }

    [Theory]
    [InlineData("LLM_RATE_LIMITED", "42")]
    [InlineData("LLM_UNAVAILABLE", null)]
    [InlineData("TARGET_DB_UNAVAILABLE", null)]
    public async Task Engine503_IsPassedThroughAndStored(string code, string? retryAfter)
    {
        _factory.Engine.OnQuery = () =>
        {
            var reply = FakeEngine.Json(HttpStatusCode.ServiceUnavailable,
                $$$"""{"error":{"code":"{{{code}}}","message":"internal detail","correlation_id":"x"}}""");
            if (retryAfter is not null)
            {
                reply.Headers.RetryAfter = new RetryConditionHeaderValue(TimeSpan.FromSeconds(int.Parse(retryAfter, System.Globalization.CultureInfo.InvariantCulture)));
            }

            return reply;
        };

        using var response = await Ask(Question, correlationId: "44444444-2222-4333-8444-555555555555");

        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.Equal(code, await ErrorCode(response));
        Assert.Equal(retryAfter, response.Headers.RetryAfter?.Delta?.TotalSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture));
        Assert.Equal("error", (await SingleHistoryFor("44444444-2222-4333-8444-555555555555")).Status);
    }

    [Theory]
    [InlineData(HttpStatusCode.Unauthorized)] // key mismatch between api and engine
    [InlineData(HttpStatusCode.InternalServerError)]
    public async Task EngineUnexpectedError_Returns502(HttpStatusCode engineStatus)
    {
        _factory.Engine.OnQuery = () => FakeEngine.Json(engineStatus, """{"error":{"code":"X","message":"y"}}""");

        using var response = await Ask(Question);

        Assert.Equal(HttpStatusCode.BadGateway, response.StatusCode);
        Assert.Equal("ENGINE_UNAVAILABLE", await ErrorCode(response));
    }

    // --- validation (design §4.2) ---

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    [InlineData(1001)]
    public async Task InvalidQuestion_Returns400WithoutCallingTheEngineOrStoring(object? question)
    {
        var text = question is int length ? new string('x', length) : (string?)question;

        using var response = await Ask(text);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("VALIDATION_FAILED", await ErrorCode(response));
        Assert.Empty(_factory.Engine.Queries);
        Assert.Equal(0, await CountHistory());
    }

    [Fact]
    public async Task QuestionOfExactly1000Chars_IsAccepted()
    {
        using var response = await Ask(new string('x', 1000));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    // --- auth and rate limit ---

    [Fact]
    public async Task Ask_WithoutToken_Returns401()
    {
        using var anonymous = _factory.CreateClient();

        using var response = await anonymous.PostAsJsonAsync("/api/ask", new { question = Question });

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.Empty(_factory.Engine.Queries);
    }

    [Fact]
    public async Task TwentyFirstAskWithinAMinute_Returns429_PerUser()
    {
        for (var i = 1; i <= 20; i++)
        {
            using var ok = await Ask($"{Question} #{i}");
            Assert.Equal(HttpStatusCode.OK, ok.StatusCode);
        }

        using var limited = await Ask($"{Question} #21");

        Assert.Equal((HttpStatusCode)429, limited.StatusCode);
        Assert.Equal("RATE_LIMITED", await ErrorCode(limited));
        Assert.NotNull(limited.Headers.RetryAfter);
        Assert.Equal(20, _factory.Engine.Queries.Count); // the 21st never reached the engine
        Assert.Equal(20, await CountHistory());

        // Another user has their own budget.
        var (_, otherToken) = await SignUp("other@example.com");
        using var other = new HttpRequestMessage(HttpMethod.Post, "/api/ask") { Content = JsonContent.Create(new { question = Question }) };
        other.Headers.Authorization = new AuthenticationHeaderValue("Bearer", otherToken);
        using var otherResponse = await _client.SendAsync(other);
        Assert.Equal(HttpStatusCode.OK, otherResponse.StatusCode);
    }

    // --- health includes the engine (design §11.5) ---

    [Theory]
    [InlineData(false, "ok", HttpStatusCode.OK, "Healthy", "Healthy")]
    [InlineData(false, "degraded", HttpStatusCode.OK, "Degraded", "Degraded")]
    [InlineData(true, "ok", HttpStatusCode.OK, "Degraded", "Degraded")]
    public async Task Health_ReportsTheEngine(bool engineDown, string engineStatus, HttpStatusCode http, string overall, string engine)
    {
        _factory.Engine.Down = engineDown;
        _factory.Engine.HealthStatus = engineStatus;

        using var response = await _client.GetAsync(new Uri("/health", UriKind.Relative));

        Assert.Equal(http, response.StatusCode);
        var body = await Json(response);
        Assert.Equal(overall, body.GetProperty("status").GetString());
        Assert.Equal(engine, body.GetProperty("checks").GetProperty("engine").GetString());
        Assert.Equal("Healthy", body.GetProperty("checks").GetProperty("app_db").GetString());
    }

    // --- helpers ---

    private async Task<(string Id, string Token)> SignUp(string email)
    {
        using var register = await _client.PostAsJsonAsync("/api/auth/register", new { email, password = "correct horse battery" });
        var id = (await Json(register)).GetProperty("id").GetString()!;
        using var login = await _client.PostAsJsonAsync("/api/auth/login", new { email, password = "correct horse battery" });
        return (id, (await Json(login)).GetProperty("accessToken").GetString()!);
    }

    private async Task<HttpResponseMessage> Ask(string? question, string? correlationId = null)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, "/api/ask") { Content = JsonContent.Create(new { question }) };
        if (correlationId is not null)
        {
            request.Headers.Add("X-Correlation-ID", correlationId);
        }

        return await _client.SendAsync(request);
    }

    private async Task<QueryHistory> LoadHistory(Guid id)
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        return await db.QueryHistory.Include(h => h.Attempts).AsNoTracking().SingleAsync(h => h.Id == id);
    }

    private async Task<QueryHistory> SingleHistoryFor(string correlationId)
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        return await db.QueryHistory.Include(h => h.Attempts).AsNoTracking().SingleAsync(h => h.CorrelationId == correlationId);
    }

    private async Task<int> CountHistory()
    {
        await using var scope = _factory.Services.CreateAsyncScope();
        return await scope.ServiceProvider.GetRequiredService<AppDbContext>().QueryHistory.CountAsync();
    }

    private static async Task<JsonElement> Json(HttpResponseMessage response) =>
        JsonDocument.Parse(await response.Content.ReadAsStringAsync()).RootElement.Clone();

    private static async Task<string?> ErrorCode(HttpResponseMessage response) =>
        (await Json(response)).GetProperty("error").GetProperty("code").GetString();
}
