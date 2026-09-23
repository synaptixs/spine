const { Model, DataTypes } = require('sequelize');
const sequelize = require('./db');

class Soloist extends Model {}

Soloist.init({ name: DataTypes.STRING }, { sequelize, modelName: 'soloist' });

module.exports = { Performer: Soloist };
