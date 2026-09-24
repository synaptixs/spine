namespace Svc;

public interface IA
{
    void Ping();
}

public interface IB
{
    void Ping();
}

// Declares no Ping of its own, and inherits one from two interfaces at the same level.
public interface IBoth : IA, IB
{
}
