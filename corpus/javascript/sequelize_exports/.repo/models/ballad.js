const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

this.Ballad = sequelize.define('song', { bars: DataTypes.INTEGER });
