package svc;

// A field and a method with one name: the method keeps the id, the field takes `#field` (B25).
public class Lazy {
    private int length;

    public int length() {
        return this.length;
    }
}
