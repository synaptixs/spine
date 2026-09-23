import { Hymn, Carol, Chant } from './models/esm.mjs';
import { Galliard } from './models/esmthis.mjs';

export function tune(sequelize) {
  const { orchestra } = sequelize.models;
  Hymn.belongsTo(orchestra);
  Carol.belongsTo(orchestra);
  Chant.belongsTo(orchestra);
  Galliard.belongsTo(orchestra);
}
