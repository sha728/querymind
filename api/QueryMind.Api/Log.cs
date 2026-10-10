namespace QueryMind.Api;

/// <summary>
/// Log events (design §11.2). Source-generated; placeholder names are snake_case so the JSON
/// fields match the engine's (<c>user_id</c>, …).
/// </summary>
internal static partial class Log
{
    public const string DataCategory = "QueryMind.Api.Data";

    [LoggerMessage(Level = LogLevel.Information, Message = "user_registered {new_user_id}")]
    public static partial void UserRegistered(ILogger logger, Guid new_user_id);

    [LoggerMessage(Level = LogLevel.Information, Message = "login_succeeded {user_id}")]
    public static partial void LoginSucceeded(ILogger logger, Guid user_id);

    [LoggerMessage(Level = LogLevel.Information, Message = "login_failed")]
    public static partial void LoginFailed(ILogger logger);

    [LoggerMessage(Level = LogLevel.Information, Message = "role_changed {target_user_id} {from_role} -> {to_role}")]
    public static partial void RoleChanged(ILogger logger, Guid target_user_id, string from_role, string to_role);

    [LoggerMessage(Level = LogLevel.Information, Message = "app_db_migrated")]
    public static partial void AppDbMigrated(ILogger logger);

    [LoggerMessage(Level = LogLevel.Information, Message = "admin_seeded {new_user_id}")]
    public static partial void AdminSeeded(ILogger logger, Guid new_user_id);

    [LoggerMessage(Level = LogLevel.Information, Message = "admin_seed_not_needed")]
    public static partial void AdminSeedNotNeeded(ILogger logger);

    [LoggerMessage(Level = LogLevel.Warning, Message = "admin_seed_skipped: ADMIN_EMAIL/ADMIN_PASSWORD not set")]
    public static partial void AdminSeedSkipped(ILogger logger);
}
