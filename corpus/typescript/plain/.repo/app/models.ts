export interface Priced {
  price: number;
}

export type Currency = "GBP" | "USD";

export class Item implements Priced {
  price: number;
  currency: Currency = "GBP";

  constructor(price: number) {
    this.price = price;
  }

  describe(): string {
    return this.label();
  }

  label(): string {
    return `${this.price}`;
  }
}
