using System.Text.Json;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using QueryMind.Api;
using Serilog;

var builder = WebApplication.CreateBuilder(args);

// Logging: JSON lines to stdout (design §11.2). Tests swap LogOutput for a buffer.
builder.Services.AddSingleton(LogOutput.StandardOutput());
builder.Services.AddSerilog((services, config) =>
    JsonLogging.Configure(config, services.GetRequiredService<LogOutput>().Writer));

// Health (design §11.5, R10.3). The app DB and engine checks are added in T36 and T38.
builder.Services.AddHealthChecks();

var app = builder.Build();

app.UseMiddleware<CorrelationMiddleware>();
app.UseSerilogRequestLogging();

app.MapHealthChecks("/health", new HealthCheckOptions { ResponseWriter = HealthResponse.WriteAsync })
    .AllowAnonymous();

app.Run();

/// <summary>Entry point; public so the tests can host it with WebApplicationFactory.</summary>
public partial class Program;

internal static class HealthResponse
{
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public static Task WriteAsync(HttpContext context, HealthReport report)
    {
        context.Response.ContentType = "application/json";
        var body = new
        {
            status = report.Status.ToString(),
            checks = report.Entries.ToDictionary(e => e.Key, e => e.Value.Status.ToString()),
        };
        return context.Response.WriteAsync(JsonSerializer.Serialize(body, Json));
    }
}
