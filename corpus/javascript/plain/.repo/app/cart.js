import { rate } from './models';

export const total = (count) => count * rate();

export function discount(count) {
  return total(count) / 2;
}
