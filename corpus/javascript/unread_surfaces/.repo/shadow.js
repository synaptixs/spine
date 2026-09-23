function find() {
  return [];
}

function secret() {
  return null;
}

var api = module.exports = {};
api.find = find;

function tag(api) {
  api.tagged = true;
  return api;
}
