const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

module.exports = Object.freeze({
  Nocturne: sequelize.define('night', { bars: DataTypes.INTEGER }),
});
