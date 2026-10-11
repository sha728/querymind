using System.Text.Json;

namespace QueryMind.Api.Engine;

// The internal engine API (design §4.3). JSON is snake_case on this side; see EngineClient.Json.

public sealed record EngineQueryRequest(string Question);

public sealed record EngineColumn(string Name, string Type, string DbType);

public sealed record EngineChart(string Recommended, IReadOnlyList<string> Allowed, string? X, IReadOnlyList<string> Y);

public sealed record EngineAttempt(
    int N, string? Sql, string? ErrorCode, string? Error, string Stage, int LatencyMs, int? PromptTokens, int? CompletionTokens);

public sealed record EngineTimings(
    int? LinkingMs, int? FewshotMs, int? GenerationMs, int? ValidationMs, int? ExecutionMs, int? SummaryMs, int? PacingMs, int? TotalMs);

public sealed record EngineUsage(string? Model, int? PromptTokens, int? CompletionTokens);

public sealed record EngineQueryResponse(
    string Status,
    string? Sql,
    string? Message,
    IReadOnlyList<EngineColumn>? Columns,
    JsonElement? Rows,
    int? RowCount,
    bool? Truncated,
    EngineChart? Chart,
    string? Summary,
    IReadOnlyList<EngineAttempt> Attempts,
    EngineTimings? Timings,
    EngineUsage? Usage);

public sealed record EngineSchemaColumn(string Name, string Type, bool Nullable, bool PrimaryKey, IReadOnlyList<string> Samples);

public sealed record EngineForeignKey(IReadOnlyList<string> Columns, string RefTable, IReadOnlyList<string> RefColumns);

public sealed record EngineSchemaTable(string Name, IReadOnlyList<EngineSchemaColumn> Columns, IReadOnlyList<EngineForeignKey> ForeignKeys);

public sealed record EngineSchema(string SchemaHash, DateTimeOffset IntrospectedAt, string Dialect, IReadOnlyList<EngineSchemaTable> Tables);

internal sealed record EngineErrorBody(EngineErrorDetail? Error);

internal sealed record EngineErrorDetail(string? Code, string? Message);

/// <summary>
/// The engine could not answer: unreachable, timed out, or returned an error (design §12).
/// <see cref="StatusCode"/> and <see cref="Code"/> are what the public API returns.
/// </summary>
public sealed class EngineCallException(int statusCode, string code, string message, TimeSpan? retryAfter = null, Exception? inner = null)
    : Exception(message, inner)
{
    public int StatusCode { get; } = statusCode;
    public string Code { get; } = code;
    public TimeSpan? RetryAfter { get; } = retryAfter;
}
