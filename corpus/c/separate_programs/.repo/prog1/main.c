static int tick(void) {
  return 1;
}

int helper(void) {
  return tick();
}

int main(void) {
  return helper();
}
