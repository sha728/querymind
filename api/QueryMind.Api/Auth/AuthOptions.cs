using System.Text;
using Microsoft.Extensions.Options;

namespace QueryMind.Api.Auth;

/// <summary>
/// Auth settings from environment variables (design §4.2, §14.2, N1): <c>JWT_SIGNING_KEY</c>
/// (at least 32 bytes), <c>JWT_ISSUER</c>, <c>JWT_AUDIENCE</c>, <c>ADMIN_EMAIL</c>,
/// <c>ADMIN_PASSWORD</c>.
/// </summary>
public sealed class AuthOptions
{
    public const int MinSigningKeyBytes = 32;
    public static readonly TimeSpan TokenLifetime = TimeSpan.FromMinutes(60);

    public string SigningKey { get; set; } = "";
    public string Issuer { get; set; } = "querymind";
    public string Audience { get; set; } = "querymind";
    public string? AdminEmail { get; set; }
    public string? AdminPassword { get; set; }

    public byte[] SigningKeyBytes => Encoding.UTF8.GetBytes(SigningKey);

    public static void Bind(AuthOptions options, IConfiguration config)
    {
        options.SigningKey = config["JWT_SIGNING_KEY"] ?? "";
        options.Issuer = NonEmpty(config["JWT_ISSUER"]) ?? options.Issuer;
        options.Audience = NonEmpty(config["JWT_AUDIENCE"]) ?? options.Audience;
        options.AdminEmail = NonEmpty(config["ADMIN_EMAIL"]);
        options.AdminPassword = NonEmpty(config["ADMIN_PASSWORD"]);
    }

    private static string? NonEmpty(string? value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}

/// <summary>Refuses to start with a weak signing key or an invalid admin seed (fail fast, N1).</summary>
public sealed class AuthOptionsValidator : IValidateOptions<AuthOptions>
{
    public ValidateOptionsResult Validate(string? name, AuthOptions options)
    {
        ArgumentNullException.ThrowIfNull(options);
        var failures = new List<string>();
        if (options.SigningKeyBytes.Length < AuthOptions.MinSigningKeyBytes)
        {
            failures.Add($"JWT_SIGNING_KEY must be at least {AuthOptions.MinSigningKeyBytes} bytes.");
        }

        if ((options.AdminEmail is null) != (options.AdminPassword is null))
        {
            failures.Add("Set both ADMIN_EMAIL and ADMIN_PASSWORD, or neither.");
        }
        else if (options.AdminEmail is not null)
        {
            if (Credentials.EmailProblem(options.AdminEmail) is { } emailProblem)
            {
                failures.Add($"ADMIN_EMAIL: {emailProblem}");
            }

            if (Credentials.PasswordProblem(options.AdminPassword!) is { } passwordProblem)
            {
                failures.Add($"ADMIN_PASSWORD: {passwordProblem}");
            }
        }

        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);
    }
}
