function pad(value) {
  return String(value);
}

exports.pad = pad;

exports.money = function (value) {
  return "$" + pad(value);
};

exports.cash = function (value) {
  return "decoy: a renamed binding read by its local name would land here";
};

exports.tax = function (value) {
  return value * 0.2;
};
