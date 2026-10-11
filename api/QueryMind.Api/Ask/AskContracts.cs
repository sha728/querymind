using System.Text.Json;

namespace QueryMind.Api.Ask;

// Public /api/ask shapes (design §4.2), serialised camelCase.

public sealed record AskRequest(string? Question);

public sealed record AskColumn(string Name, string Type, string DbType);

public sealed record AskChart(string Recommended, IReadOnlyList<string> Allowed, string? X, IReadOnlyList<string> Y);

public sealed record AskAttempt(int N, string? Sql, string? ErrorCode, string? Error, string Stage, int LatencyMs);

public sealed record AskTimings(
    int? LinkingMs, int? GenerationMs, int? ValidationMs, int? ExecutionMs, int? SummaryMs, int? EngineTotalMs, int TotalMs);

public sealed record AskUsage(string? Model, int? PromptTokens, int? CompletionTokens);

public sealed record AskResponse(
    Guid HistoryId,
    string CorrelationId,
    string Status,
    string Question,
    string? Sql,
    IReadOnlyList<AskColumn>? Columns,
    JsonElement? Rows,
    int? RowCount,
    bool? Truncated,
    AskChart? Chart,
    string? Summary,
    string? Message,
    IReadOnlyList<AskAttempt> Attempts,
    AskTimings Timings,
    AskUsage? Usage);
