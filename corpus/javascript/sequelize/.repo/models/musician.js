const { Model, DataTypes } = require('sequelize');
const sequelize = require('./db');

class Musician extends Model {}

Musician.init({ name: DataTypes.STRING(80), age: DataTypes.INTEGER }, { sequelize, modelName: 'musician' });

module.exports = Musician;
