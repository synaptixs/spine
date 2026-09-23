class Base {
  hello() {
    return "hi";
  }
}

function makeGhost() {
  return class {};
}

module.exports = { Base, Ghost: makeGhost() };
