export function clamp<T extends number>(value: T, low: number, high: number): number {
  return value < low ? low : value > high ? high : value;
}

export function closingTag(name: string): string {
  return "</" + name + ">";
}
