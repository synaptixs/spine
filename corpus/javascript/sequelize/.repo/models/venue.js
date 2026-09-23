const { DataTypes } = require('sequelize');

module.exports = (sequelize) => {
  sequelize.define('venue', {
    id: { primaryKey: true, type: DataTypes.INTEGER(11) },
    city: DataTypes.STRING(64),
  });
};
