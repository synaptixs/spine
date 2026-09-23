const { DataTypes } = require('sequelize');
const sequelize = require('./db');

const Harmony = sequelize.define('harmony', { voice: DataTypes.STRING });
const Melody = sequelize.define('melody', { voice: DataTypes.STRING });

module.exports = Melody;
