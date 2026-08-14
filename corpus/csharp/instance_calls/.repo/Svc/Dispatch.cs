namespace Svc;

public class Dispatch {
    public string ViaParameter(Handler h) {
        return h.Run();
    }
}
