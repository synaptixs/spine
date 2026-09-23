function f() {
  return 1;
}

function g() {
  return 2;
}

exports.f = f;
const base = { g };
exports.__proto__ = base;
