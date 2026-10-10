using Serilog.Events;
using Serilog.Parsing;

namespace QueryMind.Api.Tests;

[Collection(ApiTestGroup.Name)]
public sealed class LoggingTests(PostgresServer server)
{
    [Fact]
    public async Task FreshDatabaseStartup_LogsNoErrors()
    {
        using var factory = await ApiFactory.CreateAsync(server); // brand-new, empty database
        using var client = factory.CreateClient(); // starts the app: migrate + seed

        Assert.Contains(factory.LogLines, line => line.Contains("app_db_migrated", StringComparison.Ordinal));
        Assert.DoesNotContain(factory.LogLines, line => line.Contains("\"level\":\"error\"", StringComparison.Ordinal));
    }

    [Fact]
    public void MigrationHistoryProbe_IsDropped()
    {
        var probe = EfCommandError("SELECT migration_id, product_version\nFROM \"__EFMigrationsHistory\"\nORDER BY migration_id;");

        Assert.True(JsonLogging.IsMigrationHistoryProbe(probe));
    }

    [Fact]
    public void OtherFailedCommands_AreStillLogged()
    {
        Assert.False(JsonLogging.IsMigrationHistoryProbe(EfCommandError("SELECT * FROM users WHERE nope")));
        Assert.False(JsonLogging.IsMigrationHistoryProbe(
            EfCommandError("SELECT * FROM \"__EFMigrationsHistory\"", eventId: 20101))); // not CommandError
    }

    private static LogEvent EfCommandError(string commandText, int eventId = 20102) =>
        new(
            DateTimeOffset.UtcNow,
            LogEventLevel.Error,
            exception: null,
            new MessageTemplateParser().Parse("Failed executing DbCommand {commandText}"),
            [
                new LogEventProperty("EventId", new StructureValue(
                [
                    new LogEventProperty("Id", new ScalarValue(eventId)),
                    new LogEventProperty("Name", new ScalarValue("Microsoft.EntityFrameworkCore.Database.Command.CommandError")),
                ])),
                new LogEventProperty("commandText", new ScalarValue(commandText)),
            ]);
}
