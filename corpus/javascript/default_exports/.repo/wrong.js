const { Base } = require('./base');

class Wrong extends Base {}

function bad() {
  return new Base();
}

module.exports = { Wrong, bad };
