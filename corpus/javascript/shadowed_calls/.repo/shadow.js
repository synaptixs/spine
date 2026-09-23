function helper() {
  return 1;
}

function direct() {
  return helper();
}

function viaParam(helper) {
  return helper();
}

function viaLocal() {
  const helper = () => 2;
  return helper();
}

function viaCallback(items) {
  return items.map((helper) => helper());
}
