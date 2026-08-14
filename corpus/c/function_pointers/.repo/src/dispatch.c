int handle(void) {
    return 1;
}

int run(int (*cb)(void)) {
    return cb();
}

int direct(void) {
    return handle();
}
