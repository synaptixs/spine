const express = require('express');
const users = require('./users');
const app = express();
const api = express.Router();

function health(req, res) {
  res.send('ok');
}

exports.version = function (req, res) {
  res.send('1');
};

app.get('/health', health);
app.get('/version', exports.version);
app.get('/users', users.list);
app.delete('/users', users.destroy);
app.get('/inline', (req, res) => res.send('x'));
app.get(`/computed/${1}`, health);

api.get('/items', users.list);
app.use('/api', api);
