const B = require('./base');

class Child extends B {}

function make() {
  return new B().hello();
}

module.exports = { Child, make };
