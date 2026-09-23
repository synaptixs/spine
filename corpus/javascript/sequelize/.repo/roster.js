const { Musician } = require('./models/musician');

function wire(sequelize) {
  const { instrument } = sequelize.models;
  Musician.belongsTo(instrument);
}

module.exports = { wire };
