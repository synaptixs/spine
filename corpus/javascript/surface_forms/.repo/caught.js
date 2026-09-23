function f() {
  return 1;
}

function secret() {
  return null;
}

function report(error) {
  return error;
}

var api = module.exports = { f };

try {
  f();
} catch (api) {
  report(api);
}
