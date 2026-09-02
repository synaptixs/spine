import express from "express";

const app = express();
const v1 = express.Router();

export function listOrders(req: any, res: any): string {
  return "orders";
}

// A named handler on a mounted router: endpoint at the composed path, and an EXPOSES edge.
v1.get("/orders", listOrders);

// The dominant Express shape. An endpoint, but no handler symbol to point at.
v1.post("/orders", (req: any, res: any) => {
  return 1;
});

app.use("/v1", v1);

// Unmounted router: the endpoint stands at its local path.
app.get("/health", listOrders);

// CONTROL: a computed path must yield nothing at all.
const base = "/dynamic";
app.get(`${base}/thing`, listOrders);
