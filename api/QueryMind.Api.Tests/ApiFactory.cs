using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;
using Testcontainers.PostgreSql;

namespace QueryMind.Api.Tests;

/// <summary>One PostgreSQL 16 container for all API tests; each factory gets its own database.</summary>
public sealed class PostgresServer : IAsyncLifetime
{
    private readonly PostgreSqlContainer _container = new PostgreSqlBuilder("postgres:16").Build();

    public NpgsqlConnectionStringBuilder Connection => new(_container.GetConnectionString());

    public Task InitializeAsync() => _container.StartAsync();

    public Task DisposeAsync() => _container.DisposeAsync().AsTask();

    public async Task<string> CreateDatabaseAsync()
    {
        var name = $"qm_test_{Guid.NewGuid():N}";
        await using var conn = new NpgsqlConnection(_container.GetConnectionString());
        await conn.OpenAsync();
        await using var cmd = new NpgsqlCommand($"CREATE DATABASE {name}", conn);
        await cmd.ExecuteNonQueryAsync();
        return name;
    }
}

[CollectionDefinition(Name)]
public sealed class ApiTestGroup : ICollectionFixture<PostgresServer>
{
    public const string Name = "api";
}

/// <summary>
/// Hosts the API in memory against a test database, with test auth settings, and captures its
/// log output instead of writing to stdout.
/// </summary>
public sealed class ApiFactory : WebApplicationFactory<Program>
{
    public const string SigningKey = "test-signing-key-that-is-at-least-32-bytes-long";
    public const string AdminEmail = "admin@example.com";
    public const string AdminPassword = "admin-password-1";

    private readonly StringWriter _logs = new();
    private readonly Dictionary<string, string?> _settings;

    private ApiFactory(Dictionary<string, string?> settings) => _settings = settings;

    public string DatabaseName => _settings["APP_DB_NAME"]!;

    /// <summary>A factory on a new database, or on <paramref name="database"/> to simulate a restart.</summary>
    public static async Task<ApiFactory> CreateAsync(
        PostgresServer server, IReadOnlyDictionary<string, string?>? overrides = null, string? database = null)
    {
        ArgumentNullException.ThrowIfNull(server);
        var connection = server.Connection;
        var settings = new Dictionary<string, string?>
        {
            ["APP_DB_HOST"] = connection.Host,
            ["APP_DB_PORT"] = connection.Port.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["APP_DB_NAME"] = database ?? await server.CreateDatabaseAsync(),
            ["APP_DB_USER"] = connection.Username,
            ["APP_DB_PASSWORD"] = connection.Password,
            ["JWT_SIGNING_KEY"] = SigningKey,
            ["JWT_ISSUER"] = "querymind",
            ["JWT_AUDIENCE"] = "querymind",
            ["ADMIN_EMAIL"] = AdminEmail,
            ["ADMIN_PASSWORD"] = AdminPassword,
        };
        foreach (var (key, value) in overrides ?? new Dictionary<string, string?>())
        {
            settings[key] = value;
        }

        return new ApiFactory(settings);
    }

    public IReadOnlyList<string> LogLines
    {
        get
        {
            lock (_logs)
            {
                return _logs.ToString().Split('\n', StringSplitOptions.RemoveEmptyEntries);
            }
        }
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        foreach (var (key, value) in _settings)
        {
            builder.UseSetting(key, value);
        }

        builder.ConfigureServices(services =>
            services.AddSingleton(new LogOutput(TextWriter.Synchronized(_logs))));
    }

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        if (disposing)
        {
            _logs.Dispose();
        }
    }
}
