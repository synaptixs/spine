const { DataTypes } = require('sequelize');

module.exports = (sequelize) => {
  sequelize.define('orchestra', {
    id: { primaryKey: true, type: DataTypes.INTEGER },
    name: { type: DataTypes.STRING },
  });
};
