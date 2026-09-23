const format = require('./format');
const { money } = require('./format');
const tax = require('./format').tax;
const { money: cash } = require('./format');
const fs = require('fs');

function line(value) {
  return format.pad(value) + money(value);
}

function levy(value) {
  return tax(value);
}

function alias(value) {
  return cash(value);
}

function save(text) {
  return fs.writeFileSync('out.txt', text);
}
