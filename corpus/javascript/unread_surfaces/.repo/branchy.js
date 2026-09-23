function find() {
  return [];
}

function create() {
  return {};
}

exports.create = create;
if (process.env.LEGACY) exports.find = find;
