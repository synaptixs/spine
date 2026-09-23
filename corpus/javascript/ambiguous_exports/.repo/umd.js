function find() {
  return [];
}

function save() {
  return true;
}

function hidden() {
  return null;
}

if (typeof module !== 'undefined') {
  module.exports = { find, save };
}
