module.exports = {
  area(side) {
    return side * side;
  },
  perimeter: function (side) {
    return side * 4;
  },
  diagonal: (side) => side * 1.41,
};
