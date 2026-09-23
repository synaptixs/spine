var express = require('express');
var app = module.exports = express();

function dashboard(req, res) {
  res.send('admin');
}

app.get('/admin', dashboard);
