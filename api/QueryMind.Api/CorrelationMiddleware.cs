using Serilog.Context;

namespace QueryMind.Api;

/// <summary>
/// Correlation ID for every request (design §11.1, R10.1). A valid UUID in
/// <c>X-Correlation-ID</c> is kept; a missing or invalid one is replaced with a new UUID v4.
/// The ID is put on the response header, in <see cref="HttpContext.Items"/> for handlers and
/// the engine client, and on every log line written while the request runs.
/// </summary>
public sealed class CorrelationMiddleware(RequestDelegate next)
{
    public const string HeaderName = "X-Correlation-ID";
    public const string ItemKey = "correlation_id";
    public const string LogProperty = "correlation_id";

    public async Task InvokeAsync(HttpContext context)
    {
        var correlationId = Resolve(context.Request.Headers[HeaderName].ToString());
        context.Items[ItemKey] = correlationId;
        context.Response.OnStarting(() =>
        {
            context.Response.Headers[HeaderName] = correlationId;
            return Task.CompletedTask;
        });

        using (LogContext.PushProperty(LogProperty, correlationId))
        {
            await next(context);
        }
    }

    /// <summary>The incoming value if it is a UUID (normalised to lower-case "D" form), else a new one.</summary>
    public static string Resolve(string? incoming) =>
        Guid.TryParseExact(incoming?.Trim(), "D", out var id) ? id.ToString("D") : Guid.NewGuid().ToString("D");
}

public static class CorrelationExtensions
{
    public static string CorrelationId(this HttpContext context) =>
        context.Items.TryGetValue(CorrelationMiddleware.ItemKey, out var value) && value is string id
            ? id
            : throw new InvalidOperationException("CorrelationMiddleware has not run for this request.");
}
