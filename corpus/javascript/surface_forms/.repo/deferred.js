function f() {
  return 1;
}

function g() {
  return 2;
}

function init() {
  module.exports.g = g;
}

module.exports = { f, init };
