import { Item } from "./models";

export const rate = (): number => 0.2;

export function total(item: Item): number {
  return item.price * rate();
}
