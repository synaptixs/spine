const { DataTypes } = require('sequelize');
const sequelize = require('./db');

sequelize.define('tune', { bars: DataTypes.INTEGER });
sequelize.define('Tune', { bars: DataTypes.INTEGER });

function mixin(target) {
  return target;
}

mixin(module.exports);
