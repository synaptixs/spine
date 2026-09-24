namespace App;

public interface IMailer
{
    void Send();
}

public class SmtpMailer : IMailer
{
    public void Send()
    {
    }
}

public interface IClock
{
    int Now();
}

public class SystemClock : IClock
{
    public int Now() => 0;
}

public interface IAudit
{
    void Write();
}

public class DbAudit : IAudit
{
    public void Write()
    {
    }
}

public class Worker
{
}
