using System.Diagnostics;
using QueryMind.Api.Data;
using QueryMind.Api.Engine;

namespace QueryMind.Api.Ask;

/// <summary>The outcome of one ask: a response to return, or an error to return (both recorded).</summary>
public sealed record AskOutcome(AskResponse? Response, EngineCallException? Error);

/// <summary>
/// Runs one question through the engine and records it (design §3.2, §5.1, R7.1). Used by
/// <c>/api/ask</c> and, in T39, by re-run.
/// </summary>
public sealed class AskService(EngineClient engine, AppDbContext db, ILogger<AskService> log)
{
    public const int MaxQuestionChars = 1000;

    public static string? QuestionProblem(string? question) => question?.Trim() switch
    {
        null or "" => "Enter a question.",
        { Length: > MaxQuestionChars } => $"The question is longer than {MaxQuestionChars} characters.",
        _ => null,
    };

    /// <param name="started">Started when the request entered the handler: <c>total_ms</c> (design §11.3).</param>
    public async Task<AskOutcome> AskAsync(
        string question, Guid userId, string role, string correlationId, Stopwatch started,
        Guid? rerunOfId, CancellationToken cancel)
    {
        ArgumentNullException.ThrowIfNull(started);
        question = question.Trim();
        Log.AskReceived(log, question.Length);

        EngineQueryResponse result;
        try
        {
            result = await engine.QueryAsync(question, correlationId, userId.ToString(), role, cancel);
            if (!HistoryStatus.All.Contains(result.Status) || result.Status == HistoryStatus.Error)
            {
                throw new EngineCallException(502, "ENGINE_UNAVAILABLE", $"Engine returned unknown status '{result.Status}'.");
            }
        }
        catch (EngineCallException e) when (e.StatusCode == 400)
        {
            return new AskOutcome(null, e); // the engine rejected the question itself: not recorded
        }
        catch (EngineCallException e)
        {
            // Infrastructure failure: still recorded, with status=error (design §5.1, §12).
            var totalMs = (int)started.ElapsedMilliseconds;
            Log.EngineCallFailed(log, e.Code, e.Message);
            db.QueryHistory.Add(new QueryHistory
            {
                UserId = userId,
                Question = question,
                Status = HistoryStatus.Error,
                Message = Truncate($"{e.Code}: {e.Message}", QueryAttempt.MaxErrorChars),
                TotalMs = totalMs,
                CorrelationId = correlationId,
                RerunOfId = rerunOfId,
            });
            await db.SaveChangesAsync(CancellationToken.None);
            Log.AskCompleted(log, HistoryStatus.Error, totalMs, null);
            return new AskOutcome(null, e);
        }

        var history = ToHistory(result, question, userId, correlationId, rerunOfId);
        history.TotalMs = (int)started.ElapsedMilliseconds;
        db.QueryHistory.Add(history);
        await db.SaveChangesAsync(CancellationToken.None); // record even if the client went away
        Log.AskCompleted(log, history.Status, history.TotalMs.Value, history.EngineTotalMs);
        return new AskOutcome(ToResponse(result, history), null);
    }

    private static QueryHistory ToHistory(EngineQueryResponse r, string question, Guid userId, string correlationId, Guid? rerunOfId)
    {
        var success = r.Status == HistoryStatus.Success;
        var history = new QueryHistory
        {
            UserId = userId,
            Question = question,
            Status = r.Status,
            FinalSql = r.Sql,
            Message = r.Message,
            RowCount = success ? r.RowCount : null,
            Truncated = success ? r.Truncated : null,
            ChartType = success ? r.Chart?.Recommended : null,
            LinkingMs = r.Timings?.LinkingMs,
            GenerationMs = r.Timings?.GenerationMs,
            ValidationMs = r.Timings?.ValidationMs,
            ExecutionMs = r.Timings?.ExecutionMs,
            SummaryMs = r.Timings?.SummaryMs,
            EngineTotalMs = r.Timings?.TotalMs,
            PromptTokens = r.Usage?.PromptTokens,
            CompletionTokens = r.Usage?.CompletionTokens,
            Model = r.Usage?.Model,
            CorrelationId = correlationId,
            RerunOfId = rerunOfId,
        };
        foreach (var a in r.Attempts)
        {
            history.Attempts.Add(new QueryAttempt
            {
                AttemptNo = checked((short)a.N),
                Sql = a.Sql,
                Stage = a.Stage,
                ErrorCode = a.ErrorCode,
                Error = a.Error is null ? null : Truncate(a.Error, QueryAttempt.MaxErrorChars),
                LatencyMs = a.LatencyMs,
                PromptTokens = a.PromptTokens,
                CompletionTokens = a.CompletionTokens,
            });
        }

        return history;
    }

    private static AskResponse ToResponse(EngineQueryResponse r, QueryHistory h)
    {
        var success = r.Status == HistoryStatus.Success;
        return new AskResponse(
            HistoryId: h.Id,
            CorrelationId: h.CorrelationId,
            Status: r.Status,
            Question: h.Question,
            Sql: r.Sql,
            Columns: success ? r.Columns?.Select(c => new AskColumn(c.Name, c.Type, c.DbType)).ToList() : null,
            Rows: success ? r.Rows : null,
            RowCount: h.RowCount,
            Truncated: h.Truncated,
            Chart: success && r.Chart is { } c ? new AskChart(c.Recommended, c.Allowed, c.X, c.Y) : null,
            Summary: success ? r.Summary : null,
            Message: r.Message,
            Attempts: h.Attempts.Select(a => new AskAttempt(a.AttemptNo, a.Sql, a.ErrorCode, a.Error, a.Stage, a.LatencyMs)).ToList(),
            Timings: new AskTimings(h.LinkingMs, h.GenerationMs, h.ValidationMs, h.ExecutionMs, h.SummaryMs, h.EngineTotalMs, h.TotalMs!.Value),
            Usage: r.Usage is null ? null : new AskUsage(r.Usage.Model, r.Usage.PromptTokens, r.Usage.CompletionTokens));
    }

    private static string Truncate(string value, int max) => value.Length <= max ? value : value[..max];
}
