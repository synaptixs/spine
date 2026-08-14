package app;

public class Cart implements Pricer {
    private int total;

    public int subtotal() {
        return 1;
    }

    public int compute() {
        return subtotal();
    }
}
