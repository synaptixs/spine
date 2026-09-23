const math = require('./math');
const shapes = require('./shapes');
const aliasing = require('./aliasing');

var app = exports = module.exports = {};

app.run = function run(n) {
  return math.cube(n) + shapes.area(n) + math.missing(n);
};

app.whole = function whole() {
  return shapes();
};

app.go = function go() {
  return aliasing.run();
};
