"""A shopping cart — plain declarations and direct calls, no indirection."""

from shop.tax import rate


class Cart:
    """Holds line items and totals them."""

    currency: str = "GBP"

    def __init__(self) -> None:
        self.items: list[float] = []

    def add(self, price: float) -> None:
        """Append one line item."""
        self.items.append(price)

    def subtotal(self) -> float:
        """Sum of the line items, before tax."""
        return sum(self.items)

    def total(self) -> float:
        """Subtotal plus tax."""
        return apply_tax(self.subtotal())


def apply_tax(amount: float) -> float:
    """Add tax at the standard rate."""
    return amount + amount * rate()
