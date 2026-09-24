package app.svc;

import app.api.*;
import app.data.BaseRepo;
import app.data.DbStore;

public class Orders extends BaseRepo implements Store {
    private final Store backing;

    public Orders(Store backing) {
        this.backing = backing;
    }

    public int load(int id) {
        return backing.load(id);
    }

    public String name() {
        return this.backing.name();
    }

    public int viaParameter(DbStore s) {
        return s.count();
    }

    public int viaLocal() {
        DbStore local = new DbStore();
        return local.count();
    }

    public int viaVarNew() {
        var v = new DbStore();
        return v.count();
    }

    public int viaInheritedField() {
        return store.count();
    }

    public void viaInheritedMemberType(Listener l) {
        l.changed();
    }

    class Inner {
        int viaEnclosingField() {
            return backing.load(1);
        }
    }
}
