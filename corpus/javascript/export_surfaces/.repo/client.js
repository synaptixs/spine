const svc = require('./svc');
const Base = require('./base');
const merged = require('./merged');

class Impl extends Base {}

function go(ids) {
  ids.forEach(function (svc) {
    svc.save();
  });
  svc.find();
  svc.create();
  return merged.merge();
}
