class Cart {
public:
    int currency;

    int subtotal() {
        return 1;
    }

    int compute() {
        return subtotal();
    }
};
