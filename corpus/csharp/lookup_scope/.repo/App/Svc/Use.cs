using System.Collections.ObjectModel;
using Microsoft.Extensions.DependencyInjection;
using System.Windows.Forms;
using App.Model;

namespace App.Svc
{
    public class Use
    {
        public void LocalFunction()
        {
            TItem Make<TItem>() where TItem : new() => new TItem();
        }

        public void Register<DbAudit>(IServiceCollection services) where DbAudit : class, IAudit
        {
            services.AddScoped<IAudit, DbAudit>();
        }

        public void Plain() { var item = new TItem(); }
    }

    public class Panel : Control
    {
        public object Make() => new ControlCollection(this);
    }

    public class Bag : Collection<int>
    {
        public object Make() => new ControlCollection(this);
    }
}
