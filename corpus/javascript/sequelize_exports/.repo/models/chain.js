const { DataTypes } = require('sequelize');
const sequelize = require('./db');

exports.Stanza = exports.Couplet = sequelize.define('stanza', {
  lines: DataTypes.INTEGER,
});
