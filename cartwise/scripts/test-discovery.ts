import { readFileSync } from "node:fs";
import { discoverProducts } from "../src/browser/discovery.js";
import { MvpShoppingRequestSchema } from "../src/schemas.js";

const request = MvpShoppingRequestSchema.parse(
  JSON.parse(readFileSync(new URL("../src/fixtures/request.json", import.meta.url), "utf8")),
);

const discoveries = await Promise.all(
  request.suppliers.map((supplier) => discoverProducts(supplier, request.items)),
);

console.log(JSON.stringify(discoveries, null, 2));
