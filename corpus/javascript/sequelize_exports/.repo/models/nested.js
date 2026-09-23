const { DataTypes } = require('sequelize');
const sequelize = require('./db');

function mixin(target) {
  return target;
}

exports.api = {};
exports.api.Lyric = sequelize.define('verse', {
  text: DataTypes.STRING,
});
mixin(exports);
