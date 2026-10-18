import { describe, expect, it } from "vitest";
import { buildCartRequests } from "../src/core/cart-requests.js";
import { generatePlans } from "../src/core/optimizer.js";
import { fixturePipeline } from "./helpers.js";

describe("buildCartRequests", () => {
  it("groups allocations into the six unique supplier carts", () => {
    const { carts } = fixturePipeline();
    expect(carts).toHaveLength(6);
    expect(new Set(carts.map((cart) => cart.key))).toHaveLength(6);
    expect(carts.every((cart) => cart.items.every((item) => item.packs === 1))).toBe(true);
    expect(carts.filter((cart) => cart.items.length === 2)).toHaveLength(2);
  });

  it("produces identical keys for identical carts", () => {
    const { request, offers } = fixturePipeline();
    const firstPlans = generatePlans(offers, request);
    const secondPlans = generatePlans(offers, structuredClone(request));
    const firstKeys = buildCartRequests(firstPlans, request, offers).map((cart) => cart.key);
    const secondKeys = buildCartRequests(secondPlans, structuredClone(request), offers).map(
      (cart) => cart.key,
    );
    expect(secondKeys).toEqual(firstKeys);
  });

  it("changes keys when the address or quantity changes", () => {
    const { request, offers } = fixturePipeline();
    const originalPlans = generatePlans(offers, request);
    const original = buildCartRequests(originalPlans, request, offers);

    const movedRequest = structuredClone(request);
    movedRequest.address.postalCode = "90002";
    const moved = buildCartRequests(generatePlans(offers, movedRequest), movedRequest, offers);
    expect(moved.map((cart) => cart.key)).not.toEqual(original.map((cart) => cart.key));

    const changedPlans = generatePlans(offers, request);
    changedPlans[0]!.allocations[0]!.packs += 1;
    const changed = buildCartRequests(changedPlans, request, offers);
    expect(changed.map((cart) => cart.key)).not.toEqual(original.map((cart) => cart.key));
  });
});
