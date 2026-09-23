exports.list = function (req, res) {
  res.send([]);
};

function destroy(req, res) {
  res.send('private, never routed');
}

function helper(req, res) {
  res.send('deleted');
}

exports.destroy = helper;
