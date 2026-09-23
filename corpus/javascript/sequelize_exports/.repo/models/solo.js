const { DataTypes } = require('sequelize');
const sequelize = require('./db');

const Aria = sequelize.define('aria', {
  key: DataTypes.STRING,
});

module.exports = Aria;
