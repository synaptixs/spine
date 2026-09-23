function real() {
  return 1;
}

function helper() {
  return 2;
}

function secret() {
  return null;
}

const api = module.exports = { real };

for (const api of []) {
  api.run = helper;
}
