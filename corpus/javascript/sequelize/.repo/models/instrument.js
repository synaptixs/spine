const { DataTypes } = require('sequelize');

module.exports = (sequelize) => {
  sequelize.define('instrument', {
    id: { primaryKey: true, type: DataTypes.INTEGER },
    type: { type: DataTypes.STRING(40), validate: { notEmpty: true } },
  });
};
