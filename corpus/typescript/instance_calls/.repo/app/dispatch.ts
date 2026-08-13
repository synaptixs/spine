import { Handler } from "./handler";

export function viaParameter(h: Handler): string {
  return h.run();
}

export function viaLocal(): string {
  const h = new Handler();
  return h.run();
}
