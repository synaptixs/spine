int handle() {
    return 1;
}

int run(int (*handle)()) {
    return handle();
}

int direct() {
    return handle();
}
