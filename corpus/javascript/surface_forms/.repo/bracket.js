function f() {
  return 1;
}

function g() {
  return 2;
}

exports.f = f;
module['exports'].g = g;
