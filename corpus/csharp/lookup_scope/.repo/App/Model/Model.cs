namespace App.Model
{
    public interface IAudit { }

    public class DbAudit : IAudit { }

    public class TItem { }

    public class ControlCollection
    {
        public ControlCollection(object owner) { }
    }
}
