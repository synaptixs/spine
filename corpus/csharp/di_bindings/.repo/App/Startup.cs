using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace App;

public static class Startup
{
    public static void Register(IServiceCollection services)
    {
        services.AddScoped<IMailer, SmtpMailer>();
        services.TryAddSingleton<IClock, SystemClock>();
        services.AddTransient<Worker>();
        services.AddScoped<IAudit>(sp => new DbAudit());
    }
}
