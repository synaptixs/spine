const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

var api = exports = module.exports = {
  Prelude: sequelize.define('overture', { bars: DataTypes.INTEGER }),
};
api.Coda = sequelize.define('finale', { bars: DataTypes.INTEGER });
