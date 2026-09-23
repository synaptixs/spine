const Player = require('./models/musician');
const { Booking, Tour } = require('./models/booking');
const { Post, Author } = require('./models/index');
const { Tag } = require('./models/tag');
const { Comment } = require('./models/comment');
const { Story } = require('./models/article');
const { Draft } = require('./models/draft');

function applyExtraSetup(sequelize) {
  const { instrument, orchestra, venue, ghost } = sequelize.models;

  orchestra.hasMany(instrument);
  instrument.belongsTo(orchestra);
  orchestra.hasMany(venue);
  Player.belongsTo(orchestra);
  Player.belongsToMany(venue, { through: 'booking' });
  ghost.belongsTo(orchestra);
  Booking.belongsTo(orchestra);
  Tour.belongsTo(venue);
  Post.belongsTo(Author);
  Tag.belongsTo(Post);
  Comment.belongsTo(Post);
  Story.belongsTo(orchestra);
  Draft.belongsTo(orchestra);
}

module.exports = { applyExtraSetup };
