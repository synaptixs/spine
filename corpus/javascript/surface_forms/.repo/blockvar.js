function f() {
  return 1;
}

function g() {
  return 2;
}

exports.f = f;

{
  var exports = {};
  exports.g = g;
}
