import { go } from './mod.js';
import * as mod from './mod.js';

export function run() {
  return go();
}

export function halt() {
  return mod.stop();
}
