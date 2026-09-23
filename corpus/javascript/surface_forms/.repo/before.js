function f() {
  return 1;
}

function g() {
  return 2;
}

if (process.env.LEGACY) module.exports.g = g;
module.exports = { f };
