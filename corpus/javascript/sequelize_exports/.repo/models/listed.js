const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

const Hidden = sequelize.define('scherzo', { bars: DataTypes.INTEGER });
const Minuet = sequelize.define('minuet', { bars: DataTypes.INTEGER });

module.exports = { Minuet };
