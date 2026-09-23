function find() {
  return [];
}

function other() {
  return 1;
}

const base = { find };

module.exports = { __proto__: base, other };
