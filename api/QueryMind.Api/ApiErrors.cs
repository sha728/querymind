namespace QueryMind.Api;

/// <summary>
/// The error envelope used by every non-2xx response (design §4.1):
/// <c>{ "error": { "code", "message", "correlationId" } }</c>.
/// </summary>
public static class ApiErrors
{
    public sealed record Body(Detail Error);

    public sealed record Detail(string Code, string Message, string? CorrelationId);

    public static IResult Result(HttpContext context, int statusCode, string code, string message) =>
        Results.Json(Create(context, code, message), statusCode: statusCode);

    public static Task WriteAsync(HttpContext context, int statusCode, string code, string message)
    {
        ArgumentNullException.ThrowIfNull(context);
        context.Response.StatusCode = statusCode;
        return context.Response.WriteAsJsonAsync(Create(context, code, message));
    }

    private static Body Create(HttpContext context, string code, string message) =>
        new(new Detail(code, message, context.Items[CorrelationMiddleware.ItemKey] as string));
}
