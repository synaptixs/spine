const { DataTypes } = require('sequelize');
const sequelize = require('./db');

var Polka = sequelize.define('mazurka', { bars: DataTypes.INTEGER });
var Polka = sequelize.define('polka', { bars: DataTypes.INTEGER });

if (sequelize) {
  var Reel = sequelize.define('reel', { bars: DataTypes.INTEGER });
}

try {
  var Hornpipe = sequelize.define('hornpipe', { bars: DataTypes.INTEGER });
} catch (error) {}

var Galop = sequelize.define('galop', { bars: DataTypes.INTEGER });

function early(s) {
  const { orchestra } = s.models;
  Galop.belongsTo(orchestra);
}

var Galop = sequelize.define('quadrille', { bars: DataTypes.INTEGER });

var Polonaise = sequelize.define('polonaise', { bars: DataTypes.INTEGER });

(function () {
  Polonaise.belongsTo(sequelize.models.orchestra);
})();

var Polonaise = sequelize.define('bourree', { bars: DataTypes.INTEGER });

var Allemande = sequelize.define('allemande', { bars: DataTypes.INTEGER });

class Wiring {
  static {
    Allemande.belongsTo(sequelize.models.orchestra);
  }
}

var Allemande = sequelize.define('courante', { bars: DataTypes.INTEGER });

function wire(s) {
  const { orchestra } = s.models;
  Polka.belongsTo(orchestra);
  Reel.belongsTo(Polka);
}

module.exports = { Hornpipe, wire };
