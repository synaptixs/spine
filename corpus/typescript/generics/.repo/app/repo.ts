export interface Repo<T> {
  get(id: string): T;
}

export class MemoryRepo<T> implements Repo<T> {
  items: Map<string, T> = new Map();

  get(id: string): T {
    return this.items.get(id) as T;
  }
}

export function firstOf<T>(xs: T[]): T {
  return xs[0];
}
