using System.Net;
using System.Text;

namespace QueryMind.Api.Tests;

/// <summary>
/// Stands in for the Python engine (design §13.2). Records every request and answers
/// <c>/health</c> with <c>ok</c> and <c>/v1/query</c> with whatever <see cref="OnQuery"/> returns.
/// </summary>
public sealed class FakeEngine : HttpMessageHandler
{
    private readonly List<RecordedRequest> _requests = [];

    public sealed record RecordedRequest(string Path, IReadOnlyDictionary<string, string> Headers, string Body);

    public Func<HttpResponseMessage> OnQuery { get; set; } = () => Json(HttpStatusCode.OK, SuccessBody);

    public string HealthStatus { get; set; } = "ok";

    public bool Down { get; set; }

    public IReadOnlyList<RecordedRequest> Queries
    {
        get
        {
            lock (_requests)
            {
                return _requests.Where(r => r.Path == "/v1/query").ToList();
            }
        }
    }

    public const string SuccessBody = """
        {
          "status": "success",
          "sql": "SELECT company_name, sum(freight) AS total FROM orders JOIN customers USING (customer_id) GROUP BY 1 ORDER BY 2 DESC LIMIT 2",
          "message": null,
          "columns": [{ "name": "company_name", "type": "text", "db_type": "varchar" },
                      { "name": "total", "type": "numeric", "db_type": "float8" }],
          "rows": [["Ernst Handel", 12345.67], ["Save-a-lot Markets", 11002.1]],
          "row_count": 2,
          "truncated": false,
          "chart": { "recommended": "pie", "allowed": ["pie", "bar", "table"], "x": "company_name", "y": ["total"] },
          "summary": "Ernst Handel spent the most.",
          "attempts": [
            { "n": 1, "sql": "SELECT bad", "error_code": "EXECUTION_ERROR", "error": "column \"bad\" does not exist",
              "stage": "execute", "latency_ms": 700, "prompt_tokens": 1800, "completion_tokens": 60 },
            { "n": 2, "sql": "SELECT company_name ...", "error_code": null, "error": null,
              "stage": "execute", "latency_ms": 900, "prompt_tokens": 1900, "completion_tokens": 70 }
          ],
          "linking": { "mode": "auto", "applied": false, "tables": [] },
          "few_shot_ids": ["nw-04", "nw-07", "nw-14"],
          "timings": { "linking_ms": 35, "fewshot_ms": 20, "generation_ms": 1600, "validation_ms": 3,
                       "execution_ms": 92, "summary_ms": 640, "pacing_ms": 0, "total_ms": 2810 },
          "usage": { "model": "gpt-oss-120b", "prompt_tokens": 3700, "completion_tokens": 130 }
        }
        """;

    public static HttpResponseMessage Json(HttpStatusCode status, string body) =>
        new(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };

    public static string NonSuccessBody(string status, string? sql, string message, string errorCode, string stage) => $$"""
        {
          "status": "{{status}}", "sql": {{(sql is null ? "null" : $"\"{sql}\"")}}, "message": "{{message}}",
          "columns": null, "rows": null, "row_count": null, "truncated": null, "chart": null, "summary": null,
          "attempts": [{ "n": 1, "sql": {{(sql is null ? "null" : $"\"{sql}\"")}}, "error_code": "{{errorCode}}", "error": "{{message}}",
                         "stage": "{{stage}}", "latency_ms": 500, "prompt_tokens": 1500, "completion_tokens": 40 }],
          "linking": { "mode": "auto", "applied": false, "tables": [] }, "few_shot_ids": [],
          "timings": { "linking_ms": 1, "fewshot_ms": 2, "generation_ms": 480, "validation_ms": 2,
                       "execution_ms": 0, "summary_ms": 0, "pacing_ms": 0, "total_ms": 520 },
          "usage": { "model": "gpt-oss-120b", "prompt_tokens": 1500, "completion_tokens": 40 }
        }
        """;

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var path = request.RequestUri!.AbsolutePath;
        var body = request.Content is null ? "" : await request.Content.ReadAsStringAsync(cancellationToken);
        lock (_requests)
        {
            _requests.Add(new RecordedRequest(
                path,
                request.Headers.ToDictionary(h => h.Key, h => string.Join(",", h.Value), StringComparer.OrdinalIgnoreCase),
                body));
        }

        if (Down)
        {
            throw new HttpRequestException("Connection refused (engine:8000)");
        }

        return path switch
        {
            "/health" => Json(HttpStatusCode.OK, $$"""{"status":"{{HealthStatus}}"}"""),
            "/v1/query" => OnQuery(),
            _ => new HttpResponseMessage(HttpStatusCode.NotFound),
        };
    }

    // The HttpClient factory may dispose handlers when it rotates them. HttpMessageHandler keeps
    // no disposed state, so this fake keeps working for the life of its ApiFactory.
    protected override void Dispose(bool disposing) => base.Dispose(disposing);
}
