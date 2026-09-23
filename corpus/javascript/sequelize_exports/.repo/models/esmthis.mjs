import { DataTypes } from 'sequelize';
import sequelize from './db';

this.Galliard = sequelize.define('volta', { bars: DataTypes.INTEGER });
