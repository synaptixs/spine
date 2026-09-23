const frozen = require('./frozen');
const made = require('./made');
const later = require('./later');
const keyed = require('./keyed');
const grown = require('./grown');
const store = require('./store');
const branchy = require('./branchy');
const aliased = require('./aliased');
const mixed = require('./mixed');
const reflected = require('./reflected');
const shadow = require('./shadow');
const boxed = require('./boxed');
const blockmod = require('./blockmod');
const { Handler } = require('./renamed');

function go(ids) {
  frozen.find();
  frozen.create();
  frozen.secret();
  made.find();
  keyed.find();
  keyed.create();
  grown.find();
  grown.create();
  branchy.find();
  branchy.create();
  aliased.find();
  mixed.find();
  reflected.find();
  shadow.find();
  shadow.secret();
  boxed.find();
  boxed.list();
  blockmod.find();
  blockmod.list();
  new Handler().run();
  ids.forEach(function (id) {
    var store = id;
    store.save();
  });
  try {
    later.find();
  } catch (store) {
    store.save();
  }
  return store.find();
}
