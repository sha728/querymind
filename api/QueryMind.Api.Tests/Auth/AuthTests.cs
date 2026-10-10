using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using QueryMind.Api.Auth;

namespace QueryMind.Api.Tests.Auth;

[Collection(ApiTestGroup.Name)]
public sealed class AuthTests(PostgresServer server) : IAsyncLifetime
{
    private ApiFactory _factory = null!;
    private HttpClient _client = null!;

    public async Task InitializeAsync()
    {
        _factory = await ApiFactory.CreateAsync(server);
        _client = _factory.CreateClient();
    }

    public Task DisposeAsync()
    {
        _client.Dispose();
        _factory.Dispose();
        return Task.CompletedTask;
    }

    // --- register (R8.1) ---

    [Fact]
    public async Task Register_Returns201WithRoleUser()
    {
        using var response = await Register("ana@example.com", "correct horse battery");

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var body = await Json(response);
        Assert.Equal("ana@example.com", body.GetProperty("email").GetString());
        Assert.Equal("user", body.GetProperty("role").GetString());
        Assert.True(Guid.TryParse(body.GetProperty("id").GetString(), out _));
        Assert.False(body.TryGetProperty("passwordHash", out _));
    }

    [Theory]
    [InlineData("ana@example.com")]
    [InlineData("ANA@Example.COM")] // emails are case-insensitive (citext)
    [InlineData("  ana@example.com ")]
    public async Task Register_DuplicateEmail_Returns409(string duplicate)
    {
        using var first = await Register("ana@example.com", "correct horse battery");
        using var second = await Register(duplicate, "another password");

        Assert.Equal(HttpStatusCode.Conflict, second.StatusCode);
        Assert.Equal("EMAIL_TAKEN", await ErrorCode(second));
    }

    [Theory]
    [InlineData("not-an-email", "long enough password")]
    [InlineData("", "long enough password")]
    [InlineData(null, "long enough password")]
    [InlineData("Ana <ana@example.com>", "long enough password")]
    [InlineData("ana@example.com", "short")]
    [InlineData("ana@example.com", null)]
    public async Task Register_InvalidInput_Returns400(string? email, string? password)
    {
        using var response = await Register(email, password);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var error = (await Json(response)).GetProperty("error");
        Assert.Equal("VALIDATION_FAILED", error.GetProperty("code").GetString());
        Assert.False(string.IsNullOrEmpty(error.GetProperty("correlationId").GetString()));
    }

    // --- login (R8.1, design §4.2) ---

    [Fact]
    public async Task Login_ReturnsJwtWithSubEmailRoleAnd60MinuteExpiry()
    {
        using var registered = await Register("ben@example.com", "correct horse battery");
        var id = (await Json(registered)).GetProperty("id").GetString();

        using var response = await Login("BEN@example.com", "correct horse battery");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await Json(response);
        var token = new JsonWebToken(body.GetProperty("accessToken").GetString());
        Assert.Equal("HS256", token.Alg);
        Assert.Equal(id, token.Subject);
        Assert.Equal("ben@example.com", token.GetClaim("email").Value);
        Assert.Equal("user", token.GetClaim("role").Value);
        Assert.Equal(TimeSpan.FromMinutes(60), token.ValidTo - token.IssuedAt);
        Assert.Equal(token.ValidTo, body.GetProperty("expiresAt").GetDateTimeOffset().UtcDateTime, TimeSpan.FromSeconds(1));
        Assert.Equal("user", body.GetProperty("user").GetProperty("role").GetString());
    }

    [Fact]
    public async Task Login_WrongPasswordAndUnknownEmail_GiveTheSame401()
    {
        using var registered = await Register("cat@example.com", "correct horse battery");

        using var wrongPassword = await Login("cat@example.com", "wrong password!");
        using var unknownEmail = await Login("nobody@example.com", "correct horse battery");

        Assert.Equal(HttpStatusCode.Unauthorized, wrongPassword.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, unknownEmail.StatusCode);
        var a = (await Json(wrongPassword)).GetProperty("error");
        var b = (await Json(unknownEmail)).GetProperty("error");
        Assert.Equal("INVALID_CREDENTIALS", a.GetProperty("code").GetString());
        Assert.Equal(a.GetProperty("code").GetString(), b.GetProperty("code").GetString());
        Assert.Equal(a.GetProperty("message").GetString(), b.GetProperty("message").GetString());
    }

    // --- protected endpoints (R8.1) ---

    [Fact]
    public async Task ProtectedEndpoint_WithoutToken_Returns401Envelope()
    {
        using var response = await _client.PutAsJsonAsync($"/api/admin/users/{Guid.NewGuid()}/role", new { role = "admin" });

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.Equal("UNAUTHORIZED", await ErrorCode(response));
    }

    [Theory]
    [InlineData("expired")]
    [InlineData("wrong-key")]
    [InlineData("wrong-audience")]
    [InlineData("garbage")]
    public async Task ProtectedEndpoint_WithBadToken_Returns401(string kind)
    {
        var adminId = (await Json(await Login(ApiFactory.AdminEmail, ApiFactory.AdminPassword)))
            .GetProperty("user").GetProperty("id").GetString()!;
        var token = kind switch
        {
            "expired" => ForgeToken(adminId, ApiFactory.SigningKey, "querymind", DateTime.UtcNow.AddHours(-2)),
            "wrong-key" => ForgeToken(adminId, "another-signing-key-that-is-32-bytes-or-more", "querymind", DateTime.UtcNow),
            "wrong-audience" => ForgeToken(adminId, ApiFactory.SigningKey, "someone-else", DateTime.UtcNow),
            _ => "not.a.jwt",
        };

        using var response = await SetRole(token, Guid.NewGuid().ToString(), "admin");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    // --- admin seed (D10) ---

    [Fact]
    public async Task Admin_IsSeededFromSettings()
    {
        using var response = await Login(ApiFactory.AdminEmail, ApiFactory.AdminPassword);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("admin", (await Json(response)).GetProperty("user").GetProperty("role").GetString());
    }

    [Fact]
    public async Task Admin_IsSeededOnFirstStartOnly()
    {
        using (var first = await Login(ApiFactory.AdminEmail, ApiFactory.AdminPassword))
        {
            Assert.Equal(HttpStatusCode.OK, first.StatusCode);
        }

        // Restart on the same database with a different ADMIN_PASSWORD: the account is not touched.
        using var restarted = await ApiFactory.CreateAsync(
            server, new Dictionary<string, string?> { ["ADMIN_PASSWORD"] = "a-different-password" }, _factory.DatabaseName);
        using var client = restarted.CreateClient();

        using var oldPassword = await client.PostAsJsonAsync("/api/auth/login", new { email = ApiFactory.AdminEmail, password = ApiFactory.AdminPassword });
        using var newPassword = await client.PostAsJsonAsync("/api/auth/login", new { email = ApiFactory.AdminEmail, password = "a-different-password" });
        Assert.Equal(HttpStatusCode.OK, oldPassword.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, newPassword.StatusCode);
        Assert.Contains(restarted.LogLines, line => line.Contains("admin_seed_not_needed", StringComparison.Ordinal));
    }

    [Fact]
    public async Task Startup_RefusesAShortSigningKey()
    {
        using var factory = await ApiFactory.CreateAsync(server, new Dictionary<string, string?> { ["JWT_SIGNING_KEY"] = "too-short" });

        var error = Assert.Throws<OptionsValidationException>(() => factory.CreateClient());
        Assert.Contains("JWT_SIGNING_KEY", error.Message, StringComparison.Ordinal);
    }

    // --- roles (R8.2) ---

    [Fact]
    public async Task SetRole_AdminCanPromoteAUser()
    {
        var adminToken = await TokenFor(ApiFactory.AdminEmail, ApiFactory.AdminPassword);
        var userId = await RegisterId("dan@example.com");

        using var response = await SetRole(adminToken, userId, "admin");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await Json(response);
        Assert.Equal("admin", body.GetProperty("role").GetString());
        Assert.Equal(userId, body.GetProperty("id").GetString());
        // The new role is in the next token (tokens carry the role they were issued with).
        var token = new JsonWebToken(await TokenFor("dan@example.com", "correct horse battery"));
        Assert.Equal("admin", token.GetClaim("role").Value);
    }

    [Fact]
    public async Task SetRole_AsUser_Returns403()
    {
        var userId = await RegisterId("eve@example.com");
        var userToken = await TokenFor("eve@example.com", "correct horse battery");

        using var response = await SetRole(userToken, userId, "admin");

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
        Assert.Equal("FORBIDDEN", await ErrorCode(response));
    }

    [Fact]
    public async Task SetRole_OnlyAdminDemotingThemself_Returns409LastAdmin()
    {
        using var login = await Login(ApiFactory.AdminEmail, ApiFactory.AdminPassword);
        var body = await Json(login);
        var adminToken = body.GetProperty("accessToken").GetString()!;
        var adminId = body.GetProperty("user").GetProperty("id").GetString()!;

        using var response = await SetRole(adminToken, adminId, "user");

        Assert.Equal(HttpStatusCode.Conflict, response.StatusCode);
        Assert.Equal("LAST_ADMIN", await ErrorCode(response));
    }

    [Fact]
    public async Task SetRole_AdminCanStepDownOnceAnotherAdminExists()
    {
        using var login = await Login(ApiFactory.AdminEmail, ApiFactory.AdminPassword);
        var body = await Json(login);
        var adminToken = body.GetProperty("accessToken").GetString()!;
        var adminId = body.GetProperty("user").GetProperty("id").GetString()!;
        using (var promote = await SetRole(adminToken, await RegisterId("fay@example.com"), "admin"))
        {
            Assert.Equal(HttpStatusCode.OK, promote.StatusCode);
        }

        using var response = await SetRole(adminToken, adminId, "user");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("user", (await Json(response)).GetProperty("role").GetString());
    }

    [Fact]
    public async Task DemotedAdmin_LosesAdminAccessAtOnce_EvenWithTheirOldToken()
    {
        var adminToken = await TokenFor(ApiFactory.AdminEmail, ApiFactory.AdminPassword);
        var hanId = await RegisterId("han@example.com");
        using (var promote = await SetRole(adminToken, hanId, "admin"))
        {
            Assert.Equal(HttpStatusCode.OK, promote.StatusCode);
        }

        var hanAdminToken = await TokenFor("han@example.com", "correct horse battery");
        using (var demote = await SetRole(adminToken, hanId, "user"))
        {
            Assert.Equal(HttpStatusCode.OK, demote.StatusCode);
        }

        // The token still says role=admin, but the database no longer does.
        Assert.Equal("admin", new JsonWebToken(hanAdminToken).GetClaim("role").Value);
        using var response = await SetRole(hanAdminToken, await RegisterId("ivy@example.com"), "admin");

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
        Assert.Equal("FORBIDDEN", await ErrorCode(response));
    }

    [Fact]
    public async Task TokenForADeletedOrUnknownUser_IsNotAdmin()
    {
        var token = ForgeToken(Guid.NewGuid().ToString(), ApiFactory.SigningKey, "querymind", DateTime.UtcNow);

        using var response = await SetRole(token, Guid.NewGuid().ToString(), "admin");

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    [Theory]
    [InlineData("superuser", HttpStatusCode.BadRequest, "VALIDATION_FAILED")]
    [InlineData(null, HttpStatusCode.BadRequest, "VALIDATION_FAILED")]
    public async Task SetRole_InvalidRole_Returns400(string? role, HttpStatusCode status, string code)
    {
        var adminToken = await TokenFor(ApiFactory.AdminEmail, ApiFactory.AdminPassword);

        using var response = await SetRole(adminToken, await RegisterId("gus@example.com"), role);

        Assert.Equal(status, response.StatusCode);
        Assert.Equal(code, await ErrorCode(response));
    }

    [Fact]
    public async Task SetRole_UnknownUser_Returns404()
    {
        var adminToken = await TokenFor(ApiFactory.AdminEmail, ApiFactory.AdminPassword);

        using var response = await SetRole(adminToken, Guid.NewGuid().ToString(), "admin");

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    // --- logs carry the signed-in user (design §11.2) ---

    [Fact]
    public async Task SignedInRequests_AreLoggedWithUserId()
    {
        using var login = await Login(ApiFactory.AdminEmail, ApiFactory.AdminPassword);
        var body = await Json(login);
        var adminId = body.GetProperty("user").GetProperty("id").GetString()!;

        using var response = await SetRole(body.GetProperty("accessToken").GetString()!, Guid.NewGuid().ToString(), "admin");

        Assert.Contains(_factory.LogLines, line =>
            line.Contains("\"RequestPath\":\"/api/admin/users/", StringComparison.Ordinal) &&
            line.Contains($"\"user_id\":\"{adminId}\"", StringComparison.Ordinal));
        Assert.DoesNotContain(_factory.LogLines, line => line.Contains(ApiFactory.AdminPassword, StringComparison.Ordinal));
    }

    // --- helpers ---

    private Task<HttpResponseMessage> Register(string? email, string? password) =>
        _client.PostAsJsonAsync("/api/auth/register", new { email, password });

    private Task<HttpResponseMessage> Login(string email, string password) =>
        _client.PostAsJsonAsync("/api/auth/login", new { email, password });

    private async Task<string> RegisterId(string email)
    {
        using var response = await Register(email, "correct horse battery");
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        return (await Json(response)).GetProperty("id").GetString()!;
    }

    private async Task<string> TokenFor(string email, string password)
    {
        using var response = await Login(email, password);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return (await Json(response)).GetProperty("accessToken").GetString()!;
    }

    private async Task<HttpResponseMessage> SetRole(string token, string userId, string? role)
    {
        using var request = new HttpRequestMessage(HttpMethod.Put, $"/api/admin/users/{userId}/role")
        {
            Content = JsonContent.Create(new { role }),
        };
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return await _client.SendAsync(request);
    }

    private static async Task<JsonElement> Json(HttpResponseMessage response) =>
        JsonDocument.Parse(await response.Content.ReadAsStringAsync()).RootElement.Clone();

    private static async Task<string?> ErrorCode(HttpResponseMessage response) =>
        (await Json(response)).GetProperty("error").GetProperty("code").GetString();

    private static string ForgeToken(string subject, string key, string audience, DateTime issuedAt) =>
        new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
        {
            Issuer = "querymind",
            Audience = audience,
            IssuedAt = issuedAt,
            NotBefore = issuedAt,
            Expires = issuedAt.AddMinutes(60),
            Claims = new Dictionary<string, object> { ["sub"] = subject, ["role"] = "admin", ["email"] = ApiFactory.AdminEmail },
            SigningCredentials = new SigningCredentials(
                new SymmetricSecurityKey(Encoding.UTF8.GetBytes(key)), SecurityAlgorithms.HmacSha256),
        });
}
