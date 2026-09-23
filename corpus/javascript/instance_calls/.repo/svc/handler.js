class Handler {
  run() {
    return this.log();
  }
  log() {
    return "ok";
  }
}

module.exports = { Handler };
