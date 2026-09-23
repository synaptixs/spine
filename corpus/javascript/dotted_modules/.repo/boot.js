const db = require('./lib/db');

function start() {
  db.x();
  return db.config();
}
