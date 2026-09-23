import { DataTypes, Model } from 'sequelize';
import sequelize from './db';

export const Hymn = sequelize.define('anthem', { verses: DataTypes.INTEGER });

const Noel = sequelize.define('noel', { verses: DataTypes.INTEGER });

export { Noel as Carol };

export class Chant extends Model {}

Chant.init({ verses: DataTypes.INTEGER }, { sequelize, modelName: 'plainsong' });
