package app.api;

public interface Store extends Named {
    int load(int id);

    interface Listener {
        void changed();
    }
}
