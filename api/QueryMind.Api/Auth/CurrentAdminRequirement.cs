using Microsoft.AspNetCore.Authorization;
using Microsoft.EntityFrameworkCore;
using QueryMind.Api.Data;

namespace QueryMind.Api.Auth;

/// <summary>
/// Admin endpoints check the caller's role in the database on every request, not only the
/// token's <c>role</c> claim, so a demotion takes effect at once instead of when the token
/// expires (design §4.2, v0.10). The claim stays a hint for the UI.
/// </summary>
public sealed class CurrentAdminRequirement : IAuthorizationRequirement;

public sealed class CurrentAdminHandler(AppDbContext db) : AuthorizationHandler<CurrentAdminRequirement>
{
    protected override async Task HandleRequirementAsync(
        AuthorizationHandlerContext context, CurrentAdminRequirement requirement)
    {
        ArgumentNullException.ThrowIfNull(context);
        if (!Guid.TryParse(context.User.FindFirst("sub")?.Value, out var userId))
        {
            return;
        }

        var isAdmin = await db.Users.AsNoTracking()
            .AnyAsync(u => u.Id == userId && u.Role == Roles.Admin);
        if (isAdmin)
        {
            context.Succeed(requirement);
        }
    }
}
