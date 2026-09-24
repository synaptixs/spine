package app.svc;

import app.model.*;
import java.util.AbstractMap;

public abstract class MyMap<K, V> extends AbstractMap<K, V> {
    Object pair(K key, V value) {
        return new SimpleEntry<>(key, value);
    }

    Object order() {
        return new Order();
    }
}
