using System.Diagnostics;
using System.Globalization;
using System.Threading.RateLimiting;
using Microsoft.AspNetCore.RateLimiting;
using QueryMind.Api.Engine;

namespace QueryMind.Api.Ask;

/// <summary><c>POST /api/ask</c> (design §4.2, R2.1, R5, R7.1) and its per-user rate limit.</summary>
public static class AskEndpoints
{
    public const string RateLimitPolicy = "ask";
    public const int AsksPerMinute = 20;

    // Public messages for infrastructure failures (design §12): generic, with the correlation ID.
    private static readonly Dictionary<string, string> GenericMessages = new()
    {
        ["ENGINE_UNAVAILABLE"] = "The query service is unavailable. Please try again later.",
        ["ENGINE_TIMEOUT"] = "The query service took too long to answer. Please try again.",
    };

    public static void AddAskRateLimit(this IServiceCollection services) =>
        services.AddRateLimiter(options =>
        {
            options.AddPolicy(RateLimitPolicy, context => RateLimitPartition.GetFixedWindowLimiter(
                context.User.FindFirst("sub")?.Value ?? "anonymous",
                _ => new FixedWindowRateLimiterOptions
                {
                    PermitLimit = AsksPerMinute,
                    Window = TimeSpan.FromMinutes(1),
                    QueueLimit = 0,
                }));
            options.OnRejected = async (context, cancel) =>
            {
                if (context.Lease.TryGetMetadata(MetadataName.RetryAfter, out var retryAfter))
                {
                    context.HttpContext.Response.Headers.RetryAfter =
                        ((int)Math.Ceiling(retryAfter.TotalSeconds)).ToString(CultureInfo.InvariantCulture);
                }

                await ApiErrors.WriteAsync(context.HttpContext, 429, "RATE_LIMITED", "Too many questions, wait a minute.");
            };
        });

    public static void MapAskEndpoints(this IEndpointRouteBuilder app) =>
        app.MapPost("/api/ask", Ask).RequireRateLimiting(RateLimitPolicy);

    internal static async Task<IResult> Ask(AskRequest request, AskService asks, HttpContext http)
    {
        var started = Stopwatch.StartNew(); // total_ms: from entering the handler (design §11.3)
        if (AskService.QuestionProblem(request.Question) is { } problem)
        {
            return ApiErrors.Result(http, 400, "VALIDATION_FAILED", problem);
        }

        var userId = Guid.Parse(http.User.FindFirst("sub")!.Value);
        var role = http.User.FindFirst("role")?.Value ?? "user";
        var outcome = await asks.AskAsync(request.Question!, userId, role, http.CorrelationId(), started, null, http.RequestAborted);
        return outcome.Error is { } e ? ErrorResult(http, e) : Results.Ok(outcome.Response);
    }

    internal static IResult ErrorResult(HttpContext http, EngineCallException e)
    {
        if (e.RetryAfter is { } retryAfter)
        {
            http.Response.Headers.RetryAfter = ((int)Math.Ceiling(retryAfter.TotalSeconds)).ToString(CultureInfo.InvariantCulture);
        }

        var message = GenericMessages.TryGetValue(e.Code, out var generic) ? generic : e.Message;
        return ApiErrors.Result(http, e.StatusCode, e.Code, message);
    }
}
