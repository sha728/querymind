using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using Npgsql;
using QueryMind.Api.Auth;

namespace QueryMind.Api.Data;

/// <summary>App DB connection, migrations and the admin seed (design §5.1, §14.1, D10).</summary>
public static class AppDatabase
{
    /// <summary>
    /// Built from <c>APP_DB_HOST</c> (default <c>app-db</c>, the compose service), <c>APP_DB_PORT</c>
    /// (default 5432), <c>APP_DB_NAME</c>, <c>APP_DB_USER</c> and <c>APP_DB_PASSWORD</c>.
    /// </summary>
    public static string ConnectionString(IConfiguration config)
    {
        ArgumentNullException.ThrowIfNull(config);
        return new NpgsqlConnectionStringBuilder
        {
            Host = config["APP_DB_HOST"] is { Length: > 0 } host ? host : "app-db",
            Port = int.TryParse(config["APP_DB_PORT"], out var port) ? port : 5432,
            Database = config["APP_DB_NAME"] is { Length: > 0 } name ? name : "querymind",
            Username = Required(config, "APP_DB_USER"),
            Password = Required(config, "APP_DB_PASSWORD"),
        }.ConnectionString;
    }

    /// <summary>Applies pending migrations, then seeds the admin (design §14.1: on api start).</summary>
    public static async Task InitializeAsync(IServiceProvider services, CancellationToken cancel = default)
    {
        ArgumentNullException.ThrowIfNull(services);
        await using var scope = services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
        var log = scope.ServiceProvider.GetRequiredService<ILoggerFactory>().CreateLogger(Log.DataCategory);

        await db.Database.MigrateAsync(cancel);
        Log.AppDbMigrated(log);

        var auth = scope.ServiceProvider.GetRequiredService<IOptions<AuthOptions>>().Value;
        await SeedAdminAsync(db, auth, log, cancel);
    }

    /// <summary>
    /// Creates the admin from <c>ADMIN_EMAIL</c>/<c>ADMIN_PASSWORD</c> only if no account with that
    /// email exists yet (first start). Later starts never change an existing account, so a changed
    /// password or role made in the app is kept.
    /// </summary>
    internal static async Task SeedAdminAsync(AppDbContext db, AuthOptions auth, ILogger log, CancellationToken cancel)
    {
        if (auth.AdminEmail is null || auth.AdminPassword is null)
        {
            Log.AdminSeedSkipped(log);
            return;
        }

        var email = Credentials.NormalizeEmail(auth.AdminEmail);
        if (await db.Users.AnyAsync(u => u.Email == email, cancel))
        {
            Log.AdminSeedNotNeeded(log);
            return;
        }

        var admin = new User { Email = email, PasswordHash = "", Role = Roles.Admin };
        admin.PasswordHash = Credentials.Hash(admin, auth.AdminPassword);
        db.Users.Add(admin);
        await db.SaveChangesAsync(cancel);
        Log.AdminSeeded(log, admin.Id);
    }

    private static string Required(IConfiguration config, string key) =>
        config[key] is { Length: > 0 } value
            ? value
            : throw new InvalidOperationException($"{key} must be set.");
}
