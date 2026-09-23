const { DataTypes, Model } = require('sequelize');
const sequelize = require('./db');

function build() {
  class Session extends Model {}
  Session.init({ length: DataTypes.INTEGER }, { sequelize, modelName: 'take' });
  return Session;
}

const Session = null;

module.exports = { Session, build };
