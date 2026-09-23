import { Handler } from '../decoy';

export function first(): boolean {
  return new Handler().run();
}
