const { DataTypes } = require('sequelize');
const sequelize = require('./db');

(function (exports) {
  exports = { Bolero: sequelize.define('tango', { bars: DataTypes.INTEGER }) };
})(module.exports);
