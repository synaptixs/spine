import { A, B, mk } from "./svc";

export function run(): void {
  [1].forEach(() => {
    const s: A = mk();
    s.load();
  });
  [2].forEach(() => {
    const s: B = mk();
    s.save();
  });
}

export function shadowed(): void {
  const s: A = mk();
  [mk()].forEach((s) => {
    s.load();
  });
}
