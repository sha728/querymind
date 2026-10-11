using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace QueryMind.Api.Engine;

/// <summary>
/// The engine's <c>/health</c> (design §11.5). An engine that is down or degraded makes the API
/// <c>Degraded</c>, not <c>Unhealthy</c>: sign-in and history still work without it.
/// </summary>
public sealed class EngineHealthCheck(EngineClient engine) : IHealthCheck
{
    public async Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default) =>
        await engine.HealthAsync(cancellationToken) switch
        {
            "ok" => HealthCheckResult.Healthy(),
            null => HealthCheckResult.Degraded("Engine unreachable."),
            var status => HealthCheckResult.Degraded($"Engine reports {status}."),
        };
}
