const { Model, DataTypes } = require('sequelize');
const sequelize = require('./db');

function makeOther() {
  return {};
}

class Pavane extends Model {}

Pavane.init({ bars: DataTypes.INTEGER }, { sequelize, modelName: 'pavane' });

module.exports = Pavane;
Object.assign(module.exports, { Pavane: makeOther() });
