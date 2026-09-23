const { DataTypes } = require('sequelize');
const sequelize = require('./db');

var module = { exports: {} };
module.exports = { Jive: sequelize.define('shimmy', { bars: DataTypes.INTEGER }) };
