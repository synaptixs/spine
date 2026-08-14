namespace App;

public class Cart : IPricer {
    public string Currency { get; set; }

    public int Subtotal() {
        return 1;
    }

    public int Compute() {
        return Subtotal();
    }
}
