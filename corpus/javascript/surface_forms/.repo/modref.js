function f() {
  return 1;
}

function g() {
  return 2;
}

exports.f = f;
const mod = module;
mod.exports.g = g;
