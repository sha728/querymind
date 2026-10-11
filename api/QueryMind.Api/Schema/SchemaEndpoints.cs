using QueryMind.Api.Ask;
using QueryMind.Api.Auth;
using QueryMind.Api.Engine;

namespace QueryMind.Api.Schema;

public sealed record SchemaColumn(string Name, string Type, bool Nullable, bool PrimaryKey, IReadOnlyList<string> Samples);

public sealed record SchemaForeignKey(IReadOnlyList<string> Columns, string RefTable, IReadOnlyList<string> RefColumns);

public sealed record SchemaTable(string Name, IReadOnlyList<SchemaColumn> Columns, IReadOnlyList<SchemaForeignKey> ForeignKeys);

public sealed record SchemaResponse(string SchemaHash, DateTimeOffset IntrospectedAt, string Dialect, IReadOnlyList<SchemaTable> Tables);

public sealed record SchemaRefreshResponse(int Tables, string SchemaHash, DateTimeOffset IntrospectedAt);

/// <summary>The engine's cached schema for the browser, and the admin refresh (design §4.2, R1.2, R1.3).</summary>
public static class SchemaEndpoints
{
    public static void MapSchemaEndpoints(this IEndpointRouteBuilder app)
    {
        app.MapGet("/api/schema", Get);
        app.MapPost("/api/admin/schema/refresh", Refresh).RequireAuthorization(AuthEndpoints.AdminPolicy);
    }

    internal static async Task<IResult> Get(EngineClient engine, HttpContext http)
    {
        try
        {
            var schema = await engine.SchemaAsync(refresh: false, http.CorrelationId(), Caller(http), Role(http), http.RequestAborted);
            return Results.Ok(ToResponse(schema));
        }
        catch (EngineCallException e)
        {
            return AskEndpoints.ErrorResult(http, e);
        }
    }

    internal static async Task<IResult> Refresh(EngineClient engine, HttpContext http)
    {
        try
        {
            var schema = await engine.SchemaAsync(refresh: true, http.CorrelationId(), Caller(http), Role(http), http.RequestAborted);
            return Results.Ok(new SchemaRefreshResponse(schema.Tables.Count, schema.SchemaHash, schema.IntrospectedAt));
        }
        catch (EngineCallException e)
        {
            return AskEndpoints.ErrorResult(http, e);
        }
    }

    // The engine API is snake_case; the public API is camelCase (design §4.1).
    private static SchemaResponse ToResponse(EngineSchema s) => new(
        s.SchemaHash,
        s.IntrospectedAt,
        s.Dialect,
        s.Tables.Select(t => new SchemaTable(
            t.Name,
            t.Columns.Select(c => new SchemaColumn(c.Name, c.Type, c.Nullable, c.PrimaryKey, c.Samples)).ToList(),
            t.ForeignKeys.Select(f => new SchemaForeignKey(f.Columns, f.RefTable, f.RefColumns)).ToList())).ToList());

    private static string Caller(HttpContext http) => http.User.FindFirst("sub")!.Value;

    private static string Role(HttpContext http) => http.User.FindFirst("role")?.Value ?? "user";
}
