export function g(n?: number): void {}

export function h(): number {
  return 1;
}

export const concise = () => g();

export const block = () => {
  g();
};

export const nested = () => g(h());

export const shadow = (g: () => void) => g();
