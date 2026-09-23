function mixin(target, source) {
  return Object.assign(target, source);
}

function find() {
  return [];
}

function create() {
  return {};
}

exports.create = create;
mixin(exports, { find });
