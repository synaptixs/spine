const { DataTypes } = require('sequelize');
const sequelize = require('./db');

var api = exports = module.exports = {};
api.Tag = sequelize.define('tag', { label: DataTypes.STRING });
