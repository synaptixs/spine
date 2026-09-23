class Handler {
  run() {
    return false;
  }
}

class Impl {
  run() {
    return true;
  }
}

module.exports = { Handler: Impl };
