const { Handler } = require('./handler');

function viaLocal() {
  const h = new Handler();
  return h.run();
}

function viaInline() {
  return new Handler().run();
}

function viaParameter(handler) {
  return handler.run();
}

function viaReturn() {
  return new Handler();
}
