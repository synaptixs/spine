import * as db from './lib/db';

export function run(): number {
  db.x();
  return db.config();
}
