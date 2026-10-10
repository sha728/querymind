using System.Security.Claims;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using QueryMind.Api.Data;

namespace QueryMind.Api.Auth;

/// <summary>Issues HS256 JWTs with <c>sub</c>, <c>email</c>, <c>role</c> and a 60-minute lifetime (design §4.2).</summary>
public sealed class TokenService(IOptions<AuthOptions> options, TimeProvider time)
{
    public const string RoleClaim = "role";
    public const string EmailClaim = "email";

    private readonly JsonWebTokenHandler _handler = new();

    public (string Token, DateTimeOffset ExpiresAt) Issue(User user)
    {
        ArgumentNullException.ThrowIfNull(user);
        var settings = options.Value;
        var now = time.GetUtcNow();
        var expires = now + AuthOptions.TokenLifetime;
        var token = _handler.CreateToken(new SecurityTokenDescriptor
        {
            Issuer = settings.Issuer,
            Audience = settings.Audience,
            IssuedAt = now.UtcDateTime,
            NotBefore = now.UtcDateTime,
            Expires = expires.UtcDateTime,
            Subject = new ClaimsIdentity(
            [
                new Claim(JwtRegisteredClaimNames.Sub, user.Id.ToString()),
                new Claim(EmailClaim, user.Email),
                new Claim(RoleClaim, user.Role),
            ]),
            SigningCredentials = new SigningCredentials(
                new SymmetricSecurityKey(settings.SigningKeyBytes), SecurityAlgorithms.HmacSha256),
        });
        return (token, expires);
    }

    public static TokenValidationParameters ValidationParameters(AuthOptions settings)
    {
        ArgumentNullException.ThrowIfNull(settings);
        return new TokenValidationParameters
        {
            ValidIssuer = settings.Issuer,
            ValidAudience = settings.Audience,
            IssuerSigningKey = new SymmetricSecurityKey(settings.SigningKeyBytes),
            ValidAlgorithms = [SecurityAlgorithms.HmacSha256],
            ValidateIssuer = true,
            ValidateAudience = true,
            ValidateLifetime = true,
            ValidateIssuerSigningKey = true,
            RequireExpirationTime = true,
            ClockSkew = TimeSpan.FromSeconds(30),
            NameClaimType = JwtRegisteredClaimNames.Sub,
            RoleClaimType = RoleClaim,
        };
    }
}
