function f() {
  return 1;
}

function g() {
  return 2;
}

function secret() {
  return null;
}

exports.f = f;
const copy = { ...exports };
