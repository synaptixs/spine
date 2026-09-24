package app.svc;

import app.model.Base;
import app.model.Box;
import app.model.Listener;
import app.model.Order;

public class Maker {
    public void plain() {
        new Order();
    }

    public void withArgs() {
        Order o = new Order(7);
    }

    public void qualified() {
        Object o = new app.model.Order();
    }

    public void nested() {
        Order.Line line = new Order.Line();
    }

    public void generic() {
        Box<Order> b = new Box<Order>();
    }

    public void diamond() {
        Box<Order> b = new Box<>();
    }

    public void anonymousInterface() {
        Listener l = new Listener() {
            public void fired() {
                new Order();
            }
        };
    }

    public void anonymousClass() {
        Base b = new Base() {
            public void run() {
            }
        };
    }

    public Order returned() {
        return new Order();
    }

    public void array() {
        Order[] orders = new Order[3];
    }

    public void methodReference() {
        java.util.function.Supplier<Order> make = Order::new;
    }

    public void externalGeneric() {
        java.util.List<Order> orders = new java.util.ArrayList<Order>();
    }

    public void localClass() {
        class Tmp {
            Order go() {
                return new Order();
            }
        }
    }
}
