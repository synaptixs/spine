function square(n) {
  return n * n;
}

exports.cube = function (n) {
  return n * square(n);
};

module.exports.half = (n) => n / 2;
