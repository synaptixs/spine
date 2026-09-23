function f() {
  return 1;
}

function g() {
  return 2;
}

function secret() {
  return null;
}

const log = require('./logger')(module);

module.exports = { f };
