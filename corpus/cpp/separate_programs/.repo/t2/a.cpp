struct Fixture {
  int go() { return 1; }
};

int run() {
  Fixture f;
  return f.go();
}
