const umd = require('./umd');
const twice = require('./twice');

function go() {
  umd.find();
  umd.save();
  umd.hidden();
  twice.find();
  return twice.helper();
}
