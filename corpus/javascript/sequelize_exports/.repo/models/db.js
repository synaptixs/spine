const { Sequelize } = require('sequelize');

module.exports = new Sequelize('sqlite::memory:');
