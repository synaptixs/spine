function f() {
  return 1;
}

function g() {
  return 2;
}

exports.f = f;
const box = { exports };
box.exports.g = g;
