namespace QueryMind.Api.Data;

/// <summary>Allowed values of <c>users.role</c> (design §5.1, R8.2).</summary>
public static class Roles
{
    public const string User = "user";
    public const string Admin = "admin";
    public static readonly IReadOnlyList<string> All = [User, Admin];
}

/// <summary>Allowed values of <c>query_history.status</c> (design §5.1). <c>error</c> = infrastructure failure.</summary>
public static class HistoryStatus
{
    public const string Success = "success";
    public const string Failed = "failed";
    public const string Blocked = "blocked";
    public const string CannotAnswer = "cannot_answer";
    public const string Error = "error";
    public static readonly IReadOnlyList<string> All = [Success, Failed, Blocked, CannotAnswer, Error];
}

/// <summary>Allowed values of <c>query_attempts.stage</c>: where an attempt ended (design §6.2).</summary>
public static class AttemptStage
{
    public const string Extract = "extract";
    public const string Validate = "validate";
    public const string Execute = "execute";
    public static readonly IReadOnlyList<string> All = [Extract, Validate, Execute];
}

public sealed class User
{
    public Guid Id { get; set; }
    public required string Email { get; set; }
    public required string PasswordHash { get; set; }
    public string Role { get; set; } = Roles.User;
    public DateTimeOffset CreatedAt { get; set; }

    public List<QueryHistory> History { get; } = [];
}

/// <summary>One asked question and its outcome (R7.1). Result rows are not stored (E7).</summary>
public sealed class QueryHistory
{
    public Guid Id { get; set; }
    public Guid UserId { get; set; }
    public User? User { get; set; }
    public required string Question { get; set; }
    public required string Status { get; set; }
    public string? FinalSql { get; set; }
    public string? Message { get; set; }
    public int? RowCount { get; set; }
    public bool? Truncated { get; set; }
    public string? ChartType { get; set; }

    // Timings (R10.4); total_ms is measured by the .NET API.
    public int? LinkingMs { get; set; }
    public int? GenerationMs { get; set; }
    public int? ValidationMs { get; set; }
    public int? ExecutionMs { get; set; }
    public int? SummaryMs { get; set; }
    public int? EngineTotalMs { get; set; }
    public int? TotalMs { get; set; }

    // Tokens (R10.5); null when the provider did not report usage.
    public int? PromptTokens { get; set; }
    public int? CompletionTokens { get; set; }
    public string? Model { get; set; }

    public required string CorrelationId { get; set; }
    public Guid? RerunOfId { get; set; }
    public QueryHistory? RerunOf { get; set; }
    public DateTimeOffset CreatedAt { get; set; }

    public List<QueryAttempt> Attempts { get; } = [];
}

/// <summary>One generation attempt within a question (R3.3).</summary>
public sealed class QueryAttempt
{
    /// <summary>The API truncates <see cref="Error"/> to this before saving (design §5.1).</summary>
    public const int MaxErrorChars = 2000;

    public Guid Id { get; set; }
    public Guid HistoryId { get; set; }
    public QueryHistory? History { get; set; }
    public short AttemptNo { get; set; }
    public string? Sql { get; set; }
    public required string Stage { get; set; }
    public string? ErrorCode { get; set; }
    public string? Error { get; set; }
    public int LatencyMs { get; set; }
    public int? PromptTokens { get; set; }
    public int? CompletionTokens { get; set; }
}
