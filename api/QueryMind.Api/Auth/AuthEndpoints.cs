using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Npgsql;
using QueryMind.Api.Data;

namespace QueryMind.Api.Auth;

/// <summary>Log category for auth events.</summary>
public sealed class AuthEvents;

public sealed record CredentialsRequest(string? Email, string? Password);

public sealed record RoleRequest(string? Role);

public sealed record UserResponse(Guid Id, string Email, string Role)
{
    public static UserResponse From(User user) => new(user.Id, user.Email, user.Role);
}

public sealed record LoginResponse(string AccessToken, DateTimeOffset ExpiresAt, UserResponse User);

/// <summary>Register, login and role changes (design §4.2, R8.1, R8.2).</summary>
public static class AuthEndpoints
{
    public const string AdminPolicy = "admin";
    private const string InvalidCredentialsMessage = "Invalid email or password.";

    public static void MapAuthEndpoints(this IEndpointRouteBuilder app)
    {
        var auth = app.MapGroup("/api/auth").AllowAnonymous();
        auth.MapPost("/register", Register);
        auth.MapPost("/login", Login);

        app.MapPut("/api/admin/users/{id:guid}/role", SetRole).RequireAuthorization(AdminPolicy);
    }

    internal static async Task<IResult> Register(CredentialsRequest request, AppDbContext db, HttpContext http, ILogger<AuthEvents> log)
    {
        if (Credentials.EmailProblem(request.Email) is { } emailProblem)
        {
            return ApiErrors.Result(http, 400, "VALIDATION_FAILED", emailProblem);
        }

        if (Credentials.PasswordProblem(request.Password) is { } passwordProblem)
        {
            return ApiErrors.Result(http, 400, "VALIDATION_FAILED", passwordProblem);
        }

        var user = new User { Email = Credentials.NormalizeEmail(request.Email!), PasswordHash = "", Role = Roles.User };
        user.PasswordHash = Credentials.Hash(user, request.Password!);
        db.Users.Add(user);
        try
        {
            await db.SaveChangesAsync(http.RequestAborted);
        }
        catch (DbUpdateException e) when (e.InnerException is PostgresException { SqlState: PostgresErrorCodes.UniqueViolation })
        {
            return EmailTaken(http);
        }

        Log.UserRegistered(log, user.Id);
        return Results.Created($"/api/admin/users/{user.Id}", UserResponse.From(user));
    }

    internal static async Task<IResult> Login(CredentialsRequest request, AppDbContext db, TokenService tokens, HttpContext http, ILogger<AuthEvents> log)
    {
        var email = Credentials.NormalizeEmail(request.Email ?? "");
        var password = request.Password ?? "";
        // citext: the email comparison is case-insensitive in the database.
        var user = email.Length == 0 ? null : await db.Users.SingleOrDefaultAsync(u => u.Email == email, http.RequestAborted);

        var result = Credentials.Verify(user, password);
        if (user is null || result == PasswordVerificationResult.Failed)
        {
            Log.LoginFailed(log);
            return ApiErrors.Result(http, 401, "INVALID_CREDENTIALS", InvalidCredentialsMessage);
        }

        if (result == PasswordVerificationResult.SuccessRehashNeeded)
        {
            user.PasswordHash = Credentials.Hash(user, password);
            await db.SaveChangesAsync(http.RequestAborted);
        }

        var (token, expiresAt) = tokens.Issue(user);
        Log.LoginSucceeded(log, user.Id);
        return Results.Ok(new LoginResponse(token, expiresAt, UserResponse.From(user)));
    }

    internal static async Task<IResult> SetRole(Guid id, RoleRequest request, AppDbContext db, HttpContext http, ILogger<AuthEvents> log)
    {
        if (request.Role is not { } role || !Roles.All.Contains(role))
        {
            return ApiErrors.Result(http, 400, "VALIDATION_FAILED", $"Role must be one of: {string.Join(", ", Roles.All)}.");
        }

        // Serialise role changes: lock the admin rows, so two admins demoting each other at the
        // same time cannot leave the system with none.
        await using var tx = await db.Database.BeginTransactionAsync(http.RequestAborted);
        var adminIds = await db.Database
            .SqlQuery<Guid>($"SELECT id AS \"Value\" FROM users WHERE role = {Roles.Admin} FOR UPDATE")
            .ToListAsync(http.RequestAborted);
        var user = await db.Users.SingleOrDefaultAsync(u => u.Id == id, http.RequestAborted);
        if (user is null)
        {
            return ApiErrors.Result(http, 404, "NOT_FOUND", "User not found.");
        }

        if (user.Role == Roles.Admin && role == Roles.User && adminIds.Count == 1)
        {
            return ApiErrors.Result(http, 409, "LAST_ADMIN", "The only admin cannot be demoted.");
        }

        if (user.Role != role)
        {
            var previous = user.Role;
            user.Role = role;
            await db.SaveChangesAsync(http.RequestAborted);
            Log.RoleChanged(log, user.Id, previous, role);
        }

        await tx.CommitAsync(http.RequestAborted);
        return Results.Ok(UserResponse.From(user));
    }

    private static IResult EmailTaken(HttpContext http) =>
        ApiErrors.Result(http, 409, "EMAIL_TAKEN", "An account with this email already exists.");
}
