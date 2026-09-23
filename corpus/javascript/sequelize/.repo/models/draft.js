const { DataTypes } = require('sequelize');
const sequelize = require('./db');

function build() {
  const Draft = sequelize.define('draft', { body: DataTypes.STRING });
  return Draft;
}

const Draft = null;

module.exports = { Draft, build };
