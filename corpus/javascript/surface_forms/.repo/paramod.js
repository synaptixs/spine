function f() {
  return 1;
}

function g() {
  return 2;
}

function install(module) {
  module.exports = { g };
}

exports.f = f;
