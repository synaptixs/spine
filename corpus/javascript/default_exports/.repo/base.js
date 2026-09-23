class Base {
  hello() {
    return 'hi';
  }
}

const shared = new Base();

module.exports = Base;
