import { describe, it, expect } from "vitest";
import { shallowEqualObject, shallowEqualArray } from "./storeEquality";

describe("shallowEqualObject", () => {
  it("same reference is equal", () => {
    const o = { a: 1 };
    expect(shallowEqualObject(o, o)).toBe(true);
  });

  it("same keys and values are equal", () => {
    expect(shallowEqualObject({ a: 1, b: "x" }, { a: 1, b: "x" })).toBe(true);
  });

  it("differing value is not equal", () => {
    expect(shallowEqualObject({ a: 1 }, { a: 2 })).toBe(false);
  });

  it("differing key count is not equal", () => {
    expect(shallowEqualObject({ a: 1 }, { a: 1, b: 2 })).toBe(false);
  });

  it("nested object compared by reference (errs toward changed)", () => {
    expect(shallowEqualObject({ a: { x: 1 } }, { a: { x: 1 } })).toBe(false);
  });
});

describe("shallowEqualArray", () => {
  it("same reference is equal", () => {
    const arr = [{ name: "a" }];
    expect(shallowEqualArray(arr, arr)).toBe(true);
  });

  it("content-equal arrays with new references are equal", () => {
    const a = [
      { name: "a", status: "running" },
      { name: "b", status: "spawned" },
    ];
    const b = [
      { name: "a", status: "running" },
      { name: "b", status: "spawned" },
    ];
    expect(shallowEqualArray(a, b)).toBe(true);
  });

  it("differing length is not equal", () => {
    expect(shallowEqualArray([{ n: 1 }], [{ n: 1 }, { n: 2 }])).toBe(false);
  });

  it("one differing field is not equal", () => {
    const a = [{ name: "a", status: "running" }];
    const b = [{ name: "a", status: "completed" }];
    expect(shallowEqualArray(a, b)).toBe(false);
  });

  it("order matters", () => {
    const a = [{ n: 1 }, { n: 2 }];
    const b = [{ n: 2 }, { n: 1 }];
    expect(shallowEqualArray(a, b)).toBe(false);
  });

  it("scalar arrays compare by value", () => {
    expect(shallowEqualArray(["a", "b"], ["a", "b"])).toBe(true);
    expect(shallowEqualArray(["a", "b"], ["a", "c"])).toBe(false);
  });

  it("nested array element recreated each tick compares unequal", () => {
    const a = [{ paths: ["p1"] }];
    const b = [{ paths: ["p1"] }];
    expect(shallowEqualArray(a, b)).toBe(false);
  });

  it("two empty arrays are equal", () => {
    expect(shallowEqualArray([], [])).toBe(true);
  });
});
