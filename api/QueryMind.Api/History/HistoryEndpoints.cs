using System.Diagnostics;
using System.Text.Json.Serialization;
using Microsoft.EntityFrameworkCore;
using QueryMind.Api.Ask;
using QueryMind.Api.Auth;
using QueryMind.Api.Data;

namespace QueryMind.Api.History;

public sealed record HistoryItem(
    Guid Id, string Question, string Status, string? FinalSql, int? RowCount, int? TotalMs, DateTimeOffset CreatedAt,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] string? UserEmail = null);

public sealed record HistoryPage(int Page, int PageSize, int Total, IReadOnlyList<HistoryItem> Items);

public sealed record HistoryDetail(
    Guid Id,
    string Question,
    string Status,
    string? FinalSql,
    string? Message,
    int? RowCount,
    bool? Truncated,
    string? ChartType,
    int? TotalMs,
    DateTimeOffset CreatedAt,
    string CorrelationId,
    Guid? RerunOfId,
    IReadOnlyList<AskAttempt> Attempts,
    AskTimings Timings,
    AskUsage Usage,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] string? UserEmail);

/// <summary>History list, detail and re-run for users; the all-users list for admins (design §4.2, R7, R8.2).</summary>
public static class HistoryEndpoints
{
    public const int DefaultPageSize = 20;
    public const int MaxPageSize = 100;

    public static void MapHistoryEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/api/history", ListOwn);
        app.MapGet("/api/history/{id:guid}", Detail);
        app.MapPost("/api/history/{id:guid}/rerun", Rerun).RequireRateLimiting(AskEndpoints.RateLimitPolicy);
        app.MapGet("/api/admin/history", ListAll).RequireAuthorization(AuthEndpoints.AdminPolicy);
    }

    internal static async Task<IResult> ListOwn(int? page, int? pageSize, AppDbContext db, HttpContext http)
    {
        if (PagingProblem(page, pageSize) is { } problem)
        {
            return ApiErrors.Result(http, 400, "VALIDATION_FAILED", problem);
        }

        var userId = CallerId(http);
        return Results.Ok(await PageAsync(db.QueryHistory.Where(h => h.UserId == userId), page, pageSize, withEmail: false, http.RequestAborted));
    }

    internal static async Task<IResult> ListAll(Guid? userId, int? page, int? pageSize, AppDbContext db, HttpContext http)
    {
        if (PagingProblem(page, pageSize) is { } problem)
        {
            return ApiErrors.Result(http, 400, "VALIDATION_FAILED", problem);
        }

        var query = userId is { } id ? db.QueryHistory.Where(h => h.UserId == id) : db.QueryHistory;
        return Results.Ok(await PageAsync(query, page, pageSize, withEmail: true, http.RequestAborted));
    }

    internal static async Task<IResult> Detail(Guid id, AppDbContext db, HttpContext http)
    {
        var isAdmin = await IsAdminAsync(db, http);
        var history = await VisibleTo(db, http, isAdmin)
            .Include(h => h.Attempts)
            .Include(h => h.User)
            .AsNoTracking()
            .SingleOrDefaultAsync(h => h.Id == id, http.RequestAborted);
        if (history is null)
        {
            return NotFound(http); // also for another user's entry: do not reveal that it exists
        }

        return Results.Ok(new HistoryDetail(
            history.Id, history.Question, history.Status, history.FinalSql, history.Message, history.RowCount,
            history.Truncated, history.ChartType, history.TotalMs, history.CreatedAt, history.CorrelationId, history.RerunOfId,
            history.Attempts.OrderBy(a => a.AttemptNo)
                .Select(a => new AskAttempt(a.AttemptNo, a.Sql, a.ErrorCode, a.Error, a.Stage, a.LatencyMs)).ToList(),
            new AskTimings(history.LinkingMs, history.GenerationMs, history.ValidationMs, history.ExecutionMs,
                history.SummaryMs, history.EngineTotalMs, history.TotalMs ?? 0),
            new AskUsage(history.Model, history.PromptTokens, history.CompletionTokens),
            isAdmin ? history.User!.Email : null));
    }

    /// <summary>Re-asks the stored question through the full pipeline (design §4.2, assumption A2).</summary>
    internal static async Task<IResult> Rerun(Guid id, AppDbContext db, AskService asks, HttpContext http)
    {
        var started = Stopwatch.StartNew();
        var isAdmin = await IsAdminAsync(db, http);
        var question = await VisibleTo(db, http, isAdmin)
            .Where(h => h.Id == id)
            .Select(h => h.Question)
            .SingleOrDefaultAsync(http.RequestAborted);
        if (question is null)
        {
            return NotFound(http);
        }

        // The new row belongs to whoever asked for the re-run, linked to the original.
        var outcome = await asks.AskAsync(
            question, CallerId(http), isAdmin ? Roles.Admin : Roles.User, http.CorrelationId(), started, id, http.RequestAborted);
        return outcome.Error is { } e ? AskEndpoints.ErrorResult(http, e) : Results.Ok(outcome.Response);
    }

    private static IQueryable<QueryHistory> VisibleTo(AppDbContext db, HttpContext http, bool isAdmin)
    {
        var userId = CallerId(http);
        return isAdmin ? db.QueryHistory : db.QueryHistory.Where(h => h.UserId == userId);
    }

    /// <summary>Admin rights come from the app DB, not the token (design §4.2 v0.10).</summary>
    private static Task<bool> IsAdminAsync(AppDbContext db, HttpContext http)
    {
        var userId = CallerId(http);
        return db.Users.AnyAsync(u => u.Id == userId && u.Role == Roles.Admin, http.RequestAborted);
    }

    private static async Task<HistoryPage> PageAsync(
        IQueryable<QueryHistory> query, int? page, int? pageSize, bool withEmail, CancellationToken cancel)
    {
        var p = page ?? 1;
        var size = pageSize ?? DefaultPageSize;
        var total = await query.CountAsync(cancel);
        var items = await query
            .OrderByDescending(h => h.CreatedAt).ThenByDescending(h => h.Id)
            .Skip((p - 1) * size).Take(size)
            .Select(h => new HistoryItem(
                h.Id, h.Question, h.Status, h.FinalSql, h.RowCount, h.TotalMs, h.CreatedAt, withEmail ? h.User!.Email : null))
            .ToListAsync(cancel);
        return new HistoryPage(p, size, total, items);
    }

    private static string? PagingProblem(int? page, int? pageSize) =>
        page is < 1 ? "page must be 1 or more."
        : pageSize is < 1 or > MaxPageSize ? $"pageSize must be between 1 and {MaxPageSize}."
        : null;

    private static Guid CallerId(HttpContext http) => Guid.Parse(http.User.FindFirst("sub")!.Value);

    private static IResult NotFound(HttpContext http) => ApiErrors.Result(http, 404, "NOT_FOUND", "History entry not found.");
}
