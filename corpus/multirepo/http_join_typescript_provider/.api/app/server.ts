import express from "express";

const app = express();
const v1 = express.Router();

export function createOrder(req: any, res: any): string {
  return "created";
}

v1.post("/orders", createOrder);
app.use("/v1", v1);
