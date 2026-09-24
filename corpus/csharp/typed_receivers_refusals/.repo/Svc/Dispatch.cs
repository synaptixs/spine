using System.Collections.Generic;

namespace Svc;

public class Dispatch
{
    private readonly Handler _handler;

    public void ViaField() => _handler.Run();

    public void ViaUnknownVar()
    {
        var h = Make();
        h.Run();
    }

    public void ViaLambda(List<Handler> hs) => hs.ForEach(h => h.Run());

    public void ViaExtension(Handler h) => h.Shout();

    public void ViaShadow()
    {
        Rocket _handler = new Rocket();
        _handler.Run();
    }

    private Handler Make() => new Handler();
}
