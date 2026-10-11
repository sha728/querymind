using System.Text.Json;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Options;
using QueryMind.Api;
using QueryMind.Api.Ask;
using QueryMind.Api.Auth;
using QueryMind.Api.Data;
using QueryMind.Api.Engine;
using QueryMind.Api.History;
using QueryMind.Api.Schema;
using Serilog;
using Serilog.Context;

var builder = WebApplication.CreateBuilder(args);

// Logging: JSON lines to stdout (design §11.2). Tests swap LogOutput for a buffer.
builder.Services.AddSingleton(LogOutput.StandardOutput());
builder.Services.AddSerilog((services, config) =>
    JsonLogging.Configure(config, services.GetRequiredService<LogOutput>().Writer));

// App DB (design §5.1). Settings are read lazily from configuration (environment variables).
builder.Services.AddDbContext<AppDbContext>((services, options) =>
    AppDbContext.Configure(
        (DbContextOptionsBuilder<AppDbContext>)options,
        AppDatabase.ConnectionString(services.GetRequiredService<IConfiguration>())));

// Auth (design §4.2, R8): JWT bearer; every endpoint needs a user unless marked anonymous.
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton<TokenService>();
builder.Services.AddOptions<AuthOptions>()
    .Configure<IConfiguration>(AuthOptions.Bind)
    .ValidateOnStart();
builder.Services.AddSingleton<IValidateOptions<AuthOptions>, AuthOptionsValidator>();
builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme).AddJwtBearer();
builder.Services.AddOptions<JwtBearerOptions>(JwtBearerDefaults.AuthenticationScheme)
    .Configure<IOptions<AuthOptions>>((jwt, auth) =>
    {
        jwt.MapInboundClaims = false;
        jwt.TokenValidationParameters = TokenService.ValidationParameters(auth.Value);
        jwt.Events = new JwtBearerEvents
        {
            OnChallenge = async context =>
            {
                context.HandleResponse();
                await ApiErrors.WriteAsync(context.HttpContext, 401, "UNAUTHORIZED", "Sign in to continue.");
            },
            OnForbidden = context => ApiErrors.WriteAsync(context.HttpContext, 403, "FORBIDDEN", "Admins only."),
        };
    });
builder.Services.AddAuthorizationBuilder()
    .SetFallbackPolicy(new AuthorizationPolicyBuilder().RequireAuthenticatedUser().Build())
    .AddPolicy(AuthEndpoints.AdminPolicy, policy => policy
        .RequireRole(Roles.Admin) // cheap pre-check on the token
        .AddRequirements(new CurrentAdminRequirement())); // authoritative: the role in the app DB
builder.Services.AddScoped<IAuthorizationHandler, CurrentAdminHandler>();

// Engine (design §4.3): typed HttpClient with the 90 s timeout from §12.
builder.Services.AddOptions<EngineOptions>()
    .Configure<IConfiguration>(EngineOptions.Bind)
    .ValidateOnStart();
builder.Services.AddSingleton<IValidateOptions<EngineOptions>, EngineOptionsValidator>();
builder.Services.AddHttpClient<EngineClient>((services, http) =>
{
    var engine = services.GetRequiredService<IOptions<EngineOptions>>().Value;
    http.BaseAddress = new Uri(engine.BaseUrl.ToString().TrimEnd('/') + "/");
    http.Timeout = EngineOptions.Timeout;
});

// Ask (design §4.2): history persistence and the per-user rate limit.
builder.Services.AddScoped<AskService>();
builder.Services.AddAskRateLimit();

// Health (design §11.5, R10.3): app DB and engine.
builder.Services.AddHealthChecks()
    .AddDbContextCheck<AppDbContext>("app_db")
    .AddCheck<EngineHealthCheck>("engine");

var app = builder.Build();

app.UseMiddleware<CorrelationMiddleware>();
app.UseSerilogRequestLogging();
app.UseAuthentication();
app.Use(async (context, next) =>
{
    // Log context for signed-in requests (design §11.2): user_id on every line, including the
    // request-completed line written by UseSerilogRequestLogging.
    if (context.User.FindFirst("sub")?.Value is not { } userId)
    {
        await next(context);
        return;
    }

    context.RequestServices.GetRequiredService<IDiagnosticContext>().Set("user_id", userId);
    using (LogContext.PushProperty("user_id", userId))
    {
        await next(context);
    }
});
app.UseAuthorization();
app.UseRateLimiter();

app.MapHealthChecks("/health", new HealthCheckOptions { ResponseWriter = HealthResponse.WriteAsync })
    .AllowAnonymous();
app.MapAuthEndpoints();
app.MapAskEndpoints();
app.MapHistoryEndpoints();
app.MapSchemaEndpoints();

await AppDatabase.InitializeAsync(app.Services);
await app.RunAsync();

/// <summary>Entry point; public so the tests can host it with WebApplicationFactory.</summary>
public partial class Program;

internal static class HealthResponse
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public static Task WriteAsync(HttpContext context, HealthReport report)
    {
        context.Response.ContentType = "application/json";
        var body = new
        {
            status = report.Status.ToString(),
            checks = report.Entries.ToDictionary(e => e.Key, e => e.Value.Status.ToString()),
        };
        return context.Response.WriteAsync(JsonSerializer.Serialize(body, Json));
    }
}
