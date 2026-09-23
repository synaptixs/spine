const { DataTypes } = require('sequelize');
const sequelize = require('./db');

const m = {};
m.rondo = sequelize.define('rondo', { tempo: DataTypes.INTEGER });

Object.assign(module.exports, { Rondo: m.rondo });
