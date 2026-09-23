const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

function make() {
  return {};
}

module.exports.Round = sequelize.define('catch', { bars: DataTypes.INTEGER });
module.exports = { Fugue: sequelize.define('canon', { bars: DataTypes.INTEGER }) };
module.exports = make();
