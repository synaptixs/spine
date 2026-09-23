const { DataTypes } = require('sequelize');
const sequelize = require('./db');

module.exports = {
  Comment: sequelize.define('comment', { body: DataTypes.STRING }),
};
