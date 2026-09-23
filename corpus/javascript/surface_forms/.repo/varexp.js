function f() {
  return 1;
}

function g() {
  return 2;
}

var exports = {};
exports.g = g;
module.exports = { f };
