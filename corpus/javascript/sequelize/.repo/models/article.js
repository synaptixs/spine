const { DataTypes } = require('sequelize');
const sequelize = require('./db');

exports.Story = sequelize.define('article', { heading: DataTypes.STRING });

module.exports = Object.freeze({ version: 1 });
