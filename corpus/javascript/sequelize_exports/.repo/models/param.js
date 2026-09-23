const { DataTypes } = require('sequelize');
const sequelize = require('./db');

function install(module) {
  module.exports = { Gavotte: sequelize.define('jig', { bars: DataTypes.INTEGER }) };
}

exports.other = 1;
