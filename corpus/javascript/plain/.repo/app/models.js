export class Item {
  price = 0;
  describe() {
    return this.label();
  }
  label() {
    return "item";
  }
}

export function rate() {
  return 2;
}
