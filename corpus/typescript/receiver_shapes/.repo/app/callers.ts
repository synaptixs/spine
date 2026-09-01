import { Handler } from "./handler";

export function viaChain(): string {
  return new Handler().run();
}

export function viaLet(): string {
  let h: Handler = new Handler();
  return h.run();
}

export function viaField(): string {
  const box = { h: new Handler() };
  return box.h.run();
}
