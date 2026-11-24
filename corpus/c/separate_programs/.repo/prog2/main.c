int loop;

static int tick(void) {
  return loop;
}

int helper(void) {
  return tick();
}

int main(void) {
  return helper();
}
