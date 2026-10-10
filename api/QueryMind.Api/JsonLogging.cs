using Serilog;
using Serilog.Core;
using Serilog.Events;
using Serilog.Formatting;
using Serilog.Templates;

namespace QueryMind.Api;

/// <summary>
/// One JSON object per log line on stdout, with the same common fields as the engine
/// (design §11.2, R10.2): <c>ts, level, service, event, correlation_id, user_id</c>, then
/// any other properties.
/// </summary>
public static class JsonLogging
{
    public const string ServiceName = "api";

    // Level names match the engine's (structlog): debug, info, warning, error, critical.
    private const string Template =
        "{ {ts: UtcDateTime(@t), " +
        "level: if @l = 'Information' then 'info' " +
        "else if @l = 'Warning' then 'warning' " +
        "else if @l = 'Error' then 'error' " +
        "else if @l = 'Fatal' then 'critical' " +
        "else if @l = 'Debug' then 'debug' else 'trace', " +
        "service: '" + ServiceName + "', event: @m, correlation_id, user_id, exception: @x, ..rest()} }\n";

    public static ITextFormatter Formatter() => new ExpressionTemplate(Template);

    public static LoggerConfiguration Configure(LoggerConfiguration config, TextWriter output) =>
        config
            .MinimumLevel.Information()
            .MinimumLevel.Override("Microsoft.AspNetCore", LogEventLevel.Warning)
            .MinimumLevel.Override("Microsoft.Hosting.Lifetime", LogEventLevel.Information)
            // EF Core logs every SQL command at Information; keep warnings and errors only.
            .MinimumLevel.Override("Microsoft.EntityFrameworkCore", LogEventLevel.Warning)
            .Enrich.FromLogContext()
            .WriteTo.Sink(new TextWriterSink(Formatter(), output));
}

/// <summary>Where log lines go: stdout in production, a buffer in tests.</summary>
public sealed class LogOutput(TextWriter writer)
{
    public TextWriter Writer { get; } = writer;

    public static LogOutput StandardOutput() => new(Console.Out);
}

internal sealed class TextWriterSink(ITextFormatter formatter, TextWriter output) : ILogEventSink
{
    private readonly Lock _lock = new();

    public void Emit(LogEvent logEvent)
    {
        lock (_lock)
        {
            formatter.Format(logEvent, output);
            output.Flush();
        }
    }
}
