const rflx = require('./rflx');
const declared = require('./declared');
const thisy = require('./thisy');
const proto = require('./proto');
const created = require('./created');
const getter = require('./getter');
const dead = require('./dead');
const util = require('./util');
const before = require('./before');
const deferred = require('./deferred');
const blocky = require('./blocky');
const boxed = require('./boxed');
const modref = require('./modref');
const bracket = require('./bracket');
const protod = require('./protod');
const varexp = require('./varexp');
const sealed = require('./sealed');
const caught = require('./caught');
const arrowthis = require('./arrowthis');
const selfref = require('./selfref');
const varonly = require('./varonly');
const forshadow = require('./forshadow');
const namedfn = require('./namedfn');
const logged = require('./logged');
const blockvar = require('./blockvar');
const spread = require('./spread');
const orfallback = require('./orfallback');
const itself = require('./itself');
const rebound = require('./rebound');
const paramod = require('./paramod');

function go() {
  rflx.find();
  rflx.secret();
  declared.list();
  declared.secret();
  thisy.find();
  thisy.secret();
  proto.find();
  proto.other();
  created.find();
  getter.find();
  getter.secret();
  dead.stale();
  return util.util();
}

function more() {
  before.f();
  before.g();
  deferred.g();
  blocky.real();
  blocky.run();
  blocky.secret();
  boxed.g();
  modref.g();
  bracket.g();
  protod.g();
  varexp.f();
  varexp.g();
  sealed.f();
  sealed.g();
  caught.f();
  caught.secret();
  selfref.Tool();
  selfref.secret();
  varonly.g();
  return arrowthis.g();
}

function third() {
  forshadow.real();
  forshadow.run();
  forshadow.secret();
  namedfn.real();
  namedfn.run();
  namedfn.secret();
  logged.f();
  logged.secret();
  blockvar.f();
  blockvar.g();
  spread.f();
  spread.secret();
  orfallback.f();
  orfallback.g();
  itself.g();
  rebound.f();
  rebound.g();
  paramod.f();
  return paramod.g();
}

function later() {
  const Holder = class {
    static {
      var rflx = 1;
    }
  };
  return rflx.find();
}
