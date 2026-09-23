import { Handler } from '../plain';

export function other(): boolean {
  return new Handler().run();
}
