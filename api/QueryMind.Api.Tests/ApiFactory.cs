using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;

namespace QueryMind.Api.Tests;

/// <summary>Hosts the API in memory and captures its log output instead of writing to stdout.</summary>
public sealed class ApiFactory : WebApplicationFactory<Program>
{
    private readonly StringWriter _logs = new();

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

    protected override void ConfigureWebHost(IWebHostBuilder builder) =>
        builder.ConfigureServices(services =>
            services.AddSingleton(new LogOutput(TextWriter.Synchronized(_logs))));

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        if (disposing)
        {
            _logs.Dispose();
        }
    }
}
