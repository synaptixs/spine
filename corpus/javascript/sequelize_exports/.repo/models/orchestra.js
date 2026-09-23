const { DataTypes } = require('sequelize');
const sequelize = require('./db');

sequelize.define('orchestra', {
  name: DataTypes.STRING,
});
