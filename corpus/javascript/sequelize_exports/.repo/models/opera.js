const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

const Opera = sequelize.define('libretto', { acts: DataTypes.INTEGER });

module.exports = Opera;
module.exports.Opera = Opera;
