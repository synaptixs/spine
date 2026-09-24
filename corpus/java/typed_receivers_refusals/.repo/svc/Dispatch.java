package svc;

import java.util.List;

public class Dispatch {
    private final Handler handler = new Handler();

    public void viaField() {
        handler.run();
    }

    public void viaUnknownVar() {
        var r = make();
        r.run();
    }

    public void viaLambdas(List<Handler> hs) {
        hs.forEach(h -> h.run());
        hs.forEach((Handler g) -> g.run());
    }

    public void viaUnionCatch() {
        try {
            make();
        } catch (IllegalStateException | IllegalArgumentException e) {
            e.getMessage();
        }
    }

    public void viaTie(Both b) {
        b.ping();
    }

    public int viaOverride(XProto x) {
        return x.settings();
    }

    public void viaShadow() {
        Rocket handler = new Rocket();
        handler.run();
    }

    public int viaCollision(Lazy z) {
        return z.length();
    }

    private Handler make() {
        return new Handler();
    }
}
