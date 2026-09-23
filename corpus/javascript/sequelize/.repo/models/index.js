const { DataTypes } = require('sequelize');
const sequelize = require('./db');

const db = {};
db.Post = sequelize.define('post', { title: DataTypes.STRING });
db.Author = sequelize.define('author', { name: DataTypes.STRING });

module.exports = db;
