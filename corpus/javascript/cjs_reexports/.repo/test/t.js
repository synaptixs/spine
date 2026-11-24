const app = require('..');
const api = require('../api');

function use() {
  app.create();
  api.json();
  api.body.text();
}

module.exports = { use };
