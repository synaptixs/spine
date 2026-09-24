package app.data;

import app.api.Store;

public class DbStore implements Store {
    public int load(int id) {
        return id;
    }

    public String name() {
        return "db";
    }

    public int count() {
        return 1;
    }
}
