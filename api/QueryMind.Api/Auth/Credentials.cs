using System.Net.Mail;
using Microsoft.AspNetCore.Identity;
using QueryMind.Api.Data;

namespace QueryMind.Api.Auth;

/// <summary>Email and password rules (design §4.2) and password hashing (PBKDF2, design §5.1).</summary>
public static class Credentials
{
    public const int MinPasswordLength = 8;
    public const int MaxPasswordLength = 128;
    public const int MaxEmailLength = 254;

    private static readonly PasswordHasher<User> Hasher = new();

    // Verified against when the email is unknown, so both login failures take the same time.
    private static readonly string DummyHash = Hasher.HashPassword(null!, "not-a-real-password");

    public static string NormalizeEmail(string email) => email.Trim();

    public static string? EmailProblem(string? email)
    {
        var trimmed = email?.Trim() ?? "";
        if (trimmed.Length == 0 || trimmed.Length > MaxEmailLength)
        {
            return "Enter a valid email address.";
        }

        return MailAddress.TryCreate(trimmed, out var parsed) && parsed.Address == trimmed && trimmed.Contains('.', StringComparison.Ordinal)
            ? null
            : "Enter a valid email address.";
    }

    public static string? PasswordProblem(string? password) => password switch
    {
        null or { Length: < MinPasswordLength } => $"Password must be at least {MinPasswordLength} characters.",
        { Length: > MaxPasswordLength } => $"Password must be at most {MaxPasswordLength} characters.",
        _ => null,
    };

    public static string Hash(User user, string password) => Hasher.HashPassword(user, password);

    /// <summary>Checks a password; <paramref name="user"/> may be null (unknown email).</summary>
    public static PasswordVerificationResult Verify(User? user, string password)
    {
        if (user is null)
        {
            Hasher.VerifyHashedPassword(null!, DummyHash, password); // same cost as a real check
            return PasswordVerificationResult.Failed;
        }

        return Hasher.VerifyHashedPassword(user, user.PasswordHash, password);
    }
}
