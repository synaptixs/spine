const { Base } = require('./base');
const { Ghost } = require('./base');

class Impl extends Base {
  run() {
    return this.hello();
  }
}

class Local extends Impl {}

class Haunted extends Ghost {}
