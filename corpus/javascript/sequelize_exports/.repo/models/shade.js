const { DataTypes } = require('sequelize');
const sequelize = require('./db');

function mixin(target) {
  return target;
}

const db = {};

function build() {
  const db = { Hymnal: sequelize.define('psalm', { verses: DataTypes.INTEGER }) };
  return db;
}

mixin(db);

module.exports = db;
