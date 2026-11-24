import { ok, fail, extra, make, dup } from "../src";

export function use(): void {
  ok();
  fail();
  extra();
  make();
  dup();
}
