// Writes the two .xlsx files the tests read before any test runs. Both paths are
// gitignored, so the spreadsheets are generated, never committed.
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildXlsx } from "./fixtures/xlsx.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const FILES: [string, Buffer][] = [
  [
    "test/fixtures/sample.xlsx",
    buildXlsx([{ name: "Inventory", rows: [["Item", "Qty"], ["Gadgets", 7], ["Sprockets", 31]] }]),
  ],
  [
    "sample-data/Past Projects/2022/Northwind Grocers - Loyalty Pricing Review/Price Elasticity Model.xlsx",
    buildXlsx([
      {
        name: "Elasticity",
        rows: [
          ["Category", "Baseline price ($)", "Elasticity", "Current promo depth (%)", "Recommended promo depth (%)", "Margin impact (bps)"],
          ["Milk (gallon)", 3.79, -0.42, 18, 10, 14],
          ["Eggs (dozen)", 3.29, -0.55, 20, 12, 11],
          ["Bananas (lb)", 0.75, -0.61, 15, 10, 4],
          ["Fresh bread", 3.49, -0.68, 22, 14, 9],
          ["Packaged snacks", 4.19, -1.35, 25, 25, 0],
          ["Soft drinks (12 pack)", 7.99, -1.82, 30, 30, 0],
          ["Private label cereal", 2.99, -0.92, 8, 4, 22],
          ["Frozen entrees", 5.49, -1.12, 24, 20, 6],
        ],
      },
      {
        name: "Scenarios",
        rows: [
          ["Scenario", "Margin change (bps)", "Traffic change (%)", "Private label share (pts)"],
          ["Hold promotions flat", 0, 0, 0],
          ["Trim promo depth on inelastic goods", 95, -0.4, 0.8],
          ["Trim plus segment-targeted offers", 118, 0.6, 1.9],
        ],
      },
    ]),
  ],
];

export default async function setup(): Promise<void> {
  for (const [rel, data] of FILES) {
    const file = path.join(root, rel);
    await fs.mkdir(path.dirname(file), { recursive: true });
    await fs.writeFile(file, data);
  }
}
