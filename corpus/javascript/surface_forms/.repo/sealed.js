function f() {
  return 1;
}

function g() {
  return 2;
}

module.exports = Object.freeze({ f });
module.exports.g = g;
