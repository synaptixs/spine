package svc;

// A field and a method with one name share one id in this vocabulary; the field node wins.
public class Lazy {
    private int length;

    public int length() {
        return this.length;
    }
}
