const { DataTypes } = require('sequelize');
const { Performer } = require('./models/player');
const { Stanza, Couplet } = require('./models/chain');
const { Lyric } = require('./models/nested');
const { Aria } = require('./models/solo');
const Duet = require('./models/duet');
const { Rondo } = require('./models/mixed');
const { Etude, Sonata } = require('./models/stale');
const { TUNE } = require('./models/pair');
const { Chorus } = require('./models/shadow');
const { Round, Fugue } = require('./models/swap');
const { Opera } = require('./models/opera');
const { Session } = require('./models/studio');
const { Prelude, Coda } = require('./models/suite');
const { Nocturne } = require('./models/frozen');
const { Ballad } = require('./models/ballad');
const { Minuet, Scherzo } = require('./models/listed');
const Quartet = require('./models/quartet');
const { Hymnal } = require('./models/shade');
const { Gavotte } = require('./models/param');
const { Bolero } = require('./models/wrapped');
const { Hornpipe } = require('./models/twice');
const { Sarabande } = require('./models/courtly');
const { Jive } = require('./models/local');
const { Pavane } = require('./models/stately');

function draft(sequelize) {
  const Performer = sequelize.define('sketch', { lines: DataTypes.INTEGER });
  return Performer;
}

function wire(sequelize) {
  const { orchestra } = sequelize.models;
  Performer.belongsTo(orchestra);
  Stanza.belongsTo(orchestra);
  Lyric.belongsTo(orchestra);
  Aria.belongsTo(orchestra);
  Couplet.belongsTo(Stanza);
  Duet.belongsTo(orchestra);
  Rondo.belongsTo(orchestra);
  Etude.belongsTo(orchestra);
  Sonata.belongsTo(orchestra);
  TUNE.belongsTo(orchestra);
  Chorus.belongsTo(orchestra);
  Round.belongsTo(orchestra);
  Fugue.belongsTo(orchestra);
  Opera.belongsTo(orchestra);
  Session.belongsTo(orchestra);
  Prelude.belongsTo(orchestra);
  Coda.belongsTo(orchestra);
  Nocturne.belongsTo(orchestra);
  Ballad.belongsTo(orchestra);
  Minuet.belongsTo(orchestra);
  Scherzo.belongsTo(orchestra);
  Quartet.belongsTo(orchestra);
  Hymnal.belongsTo(orchestra);
  Gavotte.belongsTo(orchestra);
  Bolero.belongsTo(orchestra);
  Hornpipe.belongsTo(orchestra);
  Sarabande.belongsTo(orchestra);
  Jive.belongsTo(orchestra);
  Pavane.belongsTo(orchestra);
}

module.exports = { wire };
