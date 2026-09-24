package app.svc;

import app.model.*;

public class Use {
    static class Response extends BaseResponse {
    }

    public void localClass() {
        class Order {
        }
        new Order();
    }

    public void beforeLocal() {
        new Order();
        class Order {
        }
    }

    public void anonymous() {
        Base b = new Base() {
            public void run() {
                new Helper();
            }
        };
    }

    public void dotted() {
        new Response.Status();
    }

    public void plain() {
        new Helper();
    }
}
