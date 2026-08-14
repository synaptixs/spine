class Handler {
public:
    int format() {
        return 1;
    }

    int run() {
        return format();
    }
};

int viaParameter(Handler& h) {
    return h.run();
}
