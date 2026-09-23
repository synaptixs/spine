const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

sequelize.define('viola', { strings: DataTypes.INTEGER });
sequelize.define('cello', { strings: DataTypes.INTEGER });

module.exports = {};
