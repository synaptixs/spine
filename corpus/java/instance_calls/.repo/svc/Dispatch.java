package svc;

public class Dispatch {
    public String viaParameter(Handler h) {
        return h.run();
    }
}
