using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.Options;

namespace QueryMind.Api.Engine;

/// <summary>
/// Engine settings (design §4.3, §14.1): <c>ENGINE_BASE_URL</c> (default the compose service
/// <c>http://engine:8000</c>) and the shared secret <c>ENGINE_INTERNAL_KEY</c>.
/// </summary>
public sealed class EngineOptions
{
    public static readonly TimeSpan Timeout = TimeSpan.FromSeconds(90); // design §12

    public Uri BaseUrl { get; set; } = new("http://engine:8000");
    public string InternalKey { get; set; } = "";

    public static void Bind(EngineOptions options, IConfiguration config)
    {
        if (config["ENGINE_BASE_URL"] is { Length: > 0 } url)
        {
            options.BaseUrl = new Uri(url);
        }

        options.InternalKey = config["ENGINE_INTERNAL_KEY"] ?? "";
    }
}

public sealed class EngineOptionsValidator : IValidateOptions<EngineOptions>
{
    public ValidateOptionsResult Validate(string? name, EngineOptions options) =>
        string.IsNullOrWhiteSpace(options?.InternalKey)
            ? ValidateOptionsResult.Fail("ENGINE_INTERNAL_KEY must be set.")
            : ValidateOptionsResult.Success;
}

/// <summary>Calls the engine's internal API with the internal key and the request's correlation ID.</summary>
public sealed class EngineClient(HttpClient http, IOptions<EngineOptions> options)
{
    public const string InternalKeyHeader = "X-Internal-Key";

    internal static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web)
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    // §12: the engine's own 503 codes are passed through with these user-facing messages.
    private static readonly Dictionary<string, string> PassThrough = new()
    {
        ["LLM_UNAVAILABLE"] = "The language model is unavailable.",
        ["LLM_RATE_LIMITED"] = "The language model is busy. Try again shortly.",
        ["TARGET_DB_UNAVAILABLE"] = "Database unavailable.",
    };

    public async Task<EngineQueryResponse> QueryAsync(
        string question, string correlationId, string userId, string role, CancellationToken cancel)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, "v1/query")
        {
            Content = JsonContent.Create(new EngineQueryRequest(question), options: Json),
        };
        AddHeaders(request, correlationId, userId, role);

        using var response = await SendAsync(request, cancel);
        if (response.IsSuccessStatusCode)
        {
            var body = await ReadAsync<EngineQueryResponse>(response, cancel);
            return body ?? throw Unavailable("The engine returned an empty response.");
        }

        var error = await TryReadError(response, cancel);
        if (response.StatusCode == HttpStatusCode.ServiceUnavailable && error?.Code is { } code
            && PassThrough.TryGetValue(code, out var message))
        {
            throw new EngineCallException(503, code, message, response.Headers.RetryAfter?.Delta);
        }

        if (response.StatusCode == HttpStatusCode.BadRequest && error?.Code == "VALIDATION_FAILED")
        {
            throw new EngineCallException(400, "VALIDATION_FAILED", error.Message ?? "Invalid question.");
        }

        // 401 (key mismatch), 500 or anything unexpected: the engine is not usable right now.
        throw Unavailable($"Engine returned HTTP {(int)response.StatusCode} {error?.Code}".TrimEnd());
    }

    /// <summary>The engine's <c>/health</c> status (<c>ok</c>/<c>degraded</c>), or null if unreachable.</summary>
    public async Task<string?> HealthAsync(CancellationToken cancel)
    {
        try
        {
            using var response = await http.GetAsync(new Uri("health", UriKind.Relative), cancel);
            if (!response.IsSuccessStatusCode)
            {
                return null;
            }

            using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancel));
            return doc.RootElement.TryGetProperty("status", out var status) ? status.GetString() : null;
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException)
        {
            return null;
        }
    }

    private void AddHeaders(HttpRequestMessage request, string correlationId, string userId, string role)
    {
        request.Headers.Add(InternalKeyHeader, options.Value.InternalKey);
        request.Headers.Add(CorrelationMiddleware.HeaderName, correlationId);
        // Log context only; the engine never authorises with these (design §4.3).
        request.Headers.Add("X-User-Id", userId);
        request.Headers.Add("X-User-Role", role);
    }

    private async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancel)
    {
        try
        {
            return await http.SendAsync(request, cancel);
        }
        catch (TaskCanceledException e) when (!cancel.IsCancellationRequested)
        {
            throw new EngineCallException(504, "ENGINE_TIMEOUT", "The engine did not answer in time.", inner: e);
        }
        catch (HttpRequestException e)
        {
            throw Unavailable("The engine could not be reached.", e);
        }
    }

    private static async Task<T?> ReadAsync<T>(HttpResponseMessage response, CancellationToken cancel)
    {
        try
        {
            return await response.Content.ReadFromJsonAsync<T>(Json, cancel);
        }
        catch (JsonException e)
        {
            throw Unavailable("The engine returned a response that could not be read.", e);
        }
    }

    private static async Task<EngineErrorDetail?> TryReadError(HttpResponseMessage response, CancellationToken cancel)
    {
        try
        {
            return (await response.Content.ReadFromJsonAsync<EngineErrorBody>(Json, cancel))?.Error;
        }
        catch (Exception e) when (e is JsonException or NotSupportedException)
        {
            return null;
        }
    }

    private static EngineCallException Unavailable(string message, Exception? inner = null) =>
        new(502, "ENGINE_UNAVAILABLE", message, inner: inner);
}
