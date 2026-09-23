const { DataTypes } = require('sequelize');
const sequelize = require('./db');

function makeOther() {
  return {};
}

const Sarabande = sequelize.define('sarabande', { bars: DataTypes.INTEGER });

module.exports = Sarabande;
Object.assign(module.exports, { Sarabande: makeOther() });
