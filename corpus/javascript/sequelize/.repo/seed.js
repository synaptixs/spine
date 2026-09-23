const { Op } = require('sequelize');
const Sequelize = require('sequelize');

factory.define('fixture', { name: 'x' });
scopes.define('active', { deletedAt: Op.is });
registry.define('audit', { createdAt: Sequelize.NOW });
