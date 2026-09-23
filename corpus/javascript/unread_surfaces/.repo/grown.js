function find() {
  return [];
}

function create() {
  return {};
}

const api = { find };
Object.assign(api, { create });

module.exports = api;
