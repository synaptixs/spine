const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

const db = {};

function build() {
  const db = {};
  db.Chorus = sequelize.define('refrain', { bars: DataTypes.INTEGER });
  return db;
}

db.Chorus = null;

module.exports = db;
