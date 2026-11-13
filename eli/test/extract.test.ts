import fs from "node:fs/promises";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { UnsupportedFormatError, extractText, extractorStats } from "../src/sharepoint/extract.js";
import { makeBombDocx } from "./helpers/zip.js";

const FIXTURES = path.resolve(import.meta.dirname, "fixtures");
const load = (name: string) => fs.readFile(path.join(FIXTURES, name));
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
const BIG = 1_000_000;
const SAMPLE_PROJECTS = path.resolve(import.meta.dirname, "../sample-data/Past Projects");

/** Samples how long the event loop is blocked; returns a stop function that yields the worst stall in ms. */
function watchEventLoop(): () => number {
  let worst = 0;
  let last = performance.now();
  const timer = setInterval(() => {
    const now = performance.now();
    worst = Math.max(worst, now - last - 20);
    last = now;
  }, 20);
  return () => {
    clearInterval(timer);
    return worst;
  };
}

describe("extractText: office formats", () => {
  it("extracts pptx text from every slide", async () => {
    const r = await extractText(await load("sample.pptx"), "deck.pptx", BIG);
    expect(r.format).toBe("pptx");
    expect(r.truncated).toBe(false);
    expect(r.text).toContain("Quarterly Planning Deck");
    expect(r.text).toContain("Alpha initiative kickoff");
    expect(r.text).toContain("Roadmap Risks"); // slide 2
    expect(r.text).toContain("zebra integration");
    expect(r.totalChars).toBe(r.text.length);
  });

  it("extracts docx paragraphs and table cells", async () => {
    const r = await extractText(await load("sample.docx"), "memo.docx", BIG);
    expect(r.format).toBe("docx");
    expect(r.text).toContain("Fixture Memo Heading");
    expect(r.text).toContain("marmalade supply agreement");
    expect(r.text).toMatch(/Widgets\s*\|?\s*12/);
  });

  it("extracts xlsx cells", async () => {
    const r = await extractText(await load("sample.xlsx"), "inventory.xlsx", BIG);
    expect(r.format).toBe("xlsx");
    expect(r.text).toContain("Gadgets");
    expect(r.text).toContain("Sprockets");
    expect(r.text).toMatch(/Sprockets\s+31/);
  });

  it("extracts pdf text without any extra dependency", async () => {
    const r = await extractText(await load("sample.pdf"), "report.pdf", BIG);
    expect(r.format).toBe("pdf");
    expect(r.text).toContain("Fixture PDF Heading");
    expect(r.text).toContain("quokka migration study");
  }, 30_000);

  it("is case-insensitive about the extension and ignores directories in the name", async () => {
    const r = await extractText(await load("sample.docx"), "Past Projects/2023/MEMO.DOCX", BIG);
    expect(r.format).toBe("docx");
    expect(r.text).toContain("marmalade");
  });
});

describe("extractText: plain-text formats", () => {
  it.each(["txt", "md", "csv", "json", "html"])("decodes .%s as UTF-8 text", async (ext) => {
    const body = ext === "json" ? '{"k": "café"}' : ext === "html" ? "<p>café <b>bold</b></p>" : "café – line one\nline two";
    const r = await extractText(Buffer.from(body, "utf8"), `notes.${ext}`, BIG);
    expect(r.format).toBe(ext);
    expect(r.text).toContain("café");
    if (ext === "html") expect(r.text).toBe(body); // markup is left alone
    if (ext === "json") expect(r.text).toBe(body);
  });

  it("strips a BOM and normalizes line endings", async () => {
    const r = await extractText(Buffer.from("﻿one\r\ntwo\rthree\n", "utf8"), "a.txt", BIG);
    expect(r.text).toBe("one\ntwo\nthree");
  });

  it("strips NUL bytes", async () => {
    const r = await extractText(Buffer.from("he\u0000llo\u0000 wor\u0000ld", "utf8"), "a.txt", BIG);
    expect(r.text).toBe("hello world");
    expect(r.text).not.toContain("\u0000");
    expect(r.totalChars).toBe("hello world".length);
  });

  it("collapses runs of blank lines but keeps single paragraph breaks", async () => {
    const r = await extractText(Buffer.from("a\n\n\n\n\nb\n\nc\n \t \n\n\nd\n\n\n\n", "utf8"), "a.md", BIG);
    expect(r.text).toBe("a\n\nb\n\nc\n\nd");
  });
});

describe("extractText: unsupported and corrupt input", () => {
  it("throws UnsupportedFormatError for unknown extensions", async () => {
    const err = await extractText(Buffer.from("hi"), "archive.xyz", BIG).catch((e) => e);
    expect(err).toBeInstanceOf(UnsupportedFormatError);
    expect(err.message).toBe("Unsupported file type: .xyz");
    await expect(extractText(Buffer.from("MZ"), "tool.EXE", BIG)).rejects.toThrow("Unsupported file type: .exe");
    await expect(extractText(Buffer.from("x"), "README", BIG)).rejects.toThrow(UnsupportedFormatError);
    // Formats officeparser could read but we do not advertise stay unsupported.
    await expect(extractText(Buffer.from("x"), "old.doc", BIG)).rejects.toThrow("Unsupported file type: .doc");
  });

  it.each([
    ["not a zip at all", "broken.pptx"],
    ["PK\u0003\u0004 garbage after a zip header", "broken.docx"],
    ["", "empty.xlsx"],
    ["%PDF-1.4 this is not really a pdf", "broken.pdf"],
  ])("throws a clean UnsupportedFormatError for corrupt %j (%s)", async (content, name) => {
    const err = await extractText(Buffer.from(content, "latin1"), name, BIG).catch((e) => e);
    expect(err).toBeInstanceOf(UnsupportedFormatError);
    expect(err.name).toBe("UnsupportedFormatError");
    expect(err.message).toBe(`Could not extract text from ${name}`);
    expect(err.message).not.toMatch(/OfficeParser|zip|\n|\bat\b.*:\d+/i);
  }, 30_000);

  it("rejects a truncated real pptx", async () => {
    const full = await load("sample.pptx");
    const err = await extractText(full.subarray(0, Math.floor(full.length / 2)), "Past Projects/2023/half.pptx", BIG).catch((e) => e);
    expect(err).toBeInstanceOf(UnsupportedFormatError);
    expect(err.message).toBe("Could not extract text from half.pptx"); // base name only, no path
  });

  it("does not leak control characters from the file name into the message", async () => {
    const err = await extractText(Buffer.from("junk"), "bad\u001b[31m\nname.pptx", BIG).catch((e) => e);
    expect(err).toBeInstanceOf(UnsupportedFormatError);
    expect(err.message).not.toMatch(/[\u0000-\u001f]/);
  });
});

describe("extractText: truncation", () => {
  it("does not truncate text at or under the limit", async () => {
    const text = "x".repeat(50);
    expect(await extractText(Buffer.from(text), "a.txt", 50)).toMatchObject({ text, truncated: false, totalChars: 50 });
    expect(await extractText(Buffer.from(text), "a.txt", 51)).toMatchObject({ truncated: false, totalChars: 50 });
  });

  it("truncates to maxChars and reports the pre-truncation length", async () => {
    const r = await extractText(Buffer.from("abcdefghij".repeat(10)), "a.txt", 25);
    expect(r.text).toBe("abcdefghij".repeat(2) + "abcde");
    expect(r.truncated).toBe(true);
    expect(r.totalChars).toBe(100);
    expect(r.format).toBe("txt");
  });

  it("handles a zero limit", async () => {
    const r = await extractText(Buffer.from("abc"), "a.txt", 0);
    expect(r).toMatchObject({ text: "", truncated: true, totalChars: 3 });
  });

  it("never splits a surrogate pair at the boundary", async () => {
    const input = Buffer.from("ab\u{1F600}cd", "utf8"); // "ab", grinning face (2 UTF-16 units), "cd"
    const cut = await extractText(input, "a.txt", 3); // limit lands between the two halves
    expect(cut.text).toBe("ab");
    expect(cut.truncated).toBe(true);
    expect(cut.totalChars).toBe(6);
    expect(cut.text).not.toMatch(LONE_SURROGATE);

    const whole = await extractText(input, "a.txt", 4); // limit lands right after the emoji
    expect(whole.text).toBe("ab\u{1F600}");
    expect(whole.truncated).toBe(true);
    expect(whole.text.length).toBeLessThanOrEqual(4);
  });

  it("is code-point safe at every possible limit", async () => {
    const text = "a\u{1F600}b\u{1F9D1}‍\u{1F4BB}c\u{1F1FA}\u{1F1F8}d".repeat(3);
    for (let limit = 0; limit <= text.length + 1; limit++) {
      const r = await extractText(Buffer.from(text, "utf8"), "a.txt", limit);
      expect(r.text).not.toMatch(LONE_SURROGATE);
      expect(r.text.length).toBeLessThanOrEqual(limit);
      expect(text.startsWith(r.text)).toBe(true);
      expect(r.truncated).toBe(limit < text.length);
    }
  });

  it("truncates extracted office text too", async () => {
    const r = await extractText(await load("sample.pptx"), "deck.pptx", 20);
    expect(r.truncated).toBe(true);
    expect(r.text.length).toBeLessThanOrEqual(20);
    expect(r.totalChars).toBeGreaterThan(20);
    expect(r.text.startsWith("Quarterly Planning")).toBe(true);
  });
});

describe("extractText: hostile input and resource limits", () => {
  it("rejects a decompression bomb quickly and cleanly without stalling the event loop", async () => {
    const bomb = makeBombDocx(60); // inflates to ~60 MB, over the 48 MB limit, from a ~200 KB file
    expect(bomb.length).toBeLessThan(1024 * 1024);

    const stop = watchEventLoop();
    const started = performance.now();
    const err = await extractText(bomb, "bomb.docx", BIG).catch((e) => e);
    const elapsed = performance.now() - started;
    const worstStall = stop();

    expect(err).toBeInstanceOf(UnsupportedFormatError);
    expect(err.message).toBe("Could not extract text from bomb.docx");
    expect(elapsed).toBeLessThan(5_000);
    expect(worstStall).toBeLessThan(750);
    expect(extractorStats()).toMatchObject({ active: 0, queued: 0 });

    // The process is still healthy: a normal document parses straight afterwards.
    const ok = await extractText(await load("sample.docx"), "memo.docx", BIG);
    expect(ok.text).toContain("marmalade");
  }, 30_000);

  it("kills a runaway parse at the hard timeout without blocking the main thread", async () => {
    // Under the size limit, so the worker really starts parsing; 20 MB of XML takes seconds and gigabytes to chew through.
    const heavy = makeBombDocx(20);
    const before = extractorStats();

    const stop = watchEventLoop();
    const started = performance.now();
    const err = await extractText(heavy, "heavy.docx", BIG, { timeoutMs: 400 }).catch((e) => e);
    const elapsed = performance.now() - started;
    const worstStall = stop();

    expect(err).toBeInstanceOf(UnsupportedFormatError);
    expect(err.message).toBe("Could not extract text from heavy.docx");
    expect(elapsed).toBeLessThan(5_000);
    expect(worstStall).toBeLessThan(750);
    const after = extractorStats();
    expect(after.timedOut).toBe(before.timedOut + 1); // it was the timeout that ended it, not a crash
    expect(after.failed).toBe(before.failed);
    expect(after.active).toBe(0); // the worker was terminated and awaited before the slot was released
  }, 30_000);

  it("survives a worker that runs out of memory", async () => {
    const before = extractorStats();
    const err = await extractText(makeBombDocx(15), "big.docx", BIG).catch((e) => e);
    expect(err).toBeInstanceOf(UnsupportedFormatError);
    expect(err.message).toBe("Could not extract text from big.docx");
    expect(extractorStats().failed).toBe(before.failed + 1);
    expect((await extractText(await load("sample.pptx"), "deck.pptx", BIG)).text).toContain("Quarterly Planning Deck");
  }, 60_000);

  it("caps concurrent office parses and queues the rest", async () => {
    const docx = await load("sample.docx");
    const before = extractorStats();
    const jobs = Array.from({ length: 6 }, () => extractText(docx, "memo.docx", BIG));

    // extractText starts synchronously, so the limiter state is visible immediately.
    const midway = extractorStats();
    expect(midway.active).toBe(2);
    expect(midway.queued).toBe(4);

    const results = await Promise.all(jobs);
    for (const r of results) expect(r.text).toContain("marmalade");
    const after = extractorStats();
    expect(after.peakActive).toBeLessThanOrEqual(2);
    expect(after.started).toBe(before.started + 6);
    expect(after).toMatchObject({ active: 0, queued: 0 });
  }, 60_000);

  it("keeps plain-text formats on the main thread", async () => {
    const before = extractorStats().started;
    await extractText(Buffer.from("hello"), "a.txt", BIG);
    expect(extractorStats().started).toBe(before);
  });

  it("extracts the real sample-data pptx, docx, pdf and xlsx concurrently", async () => {
    const read = (rel: string) => fs.readFile(path.join(SAMPLE_PROJECTS, rel));
    const [pptx, docx, pdf, xlsx] = await Promise.all([
      read("2023/Acme Chat - Go-To-Market Strategy/Final Presentation.pptx").then((d) => extractText(d, "Final Presentation.pptx", BIG)),
      read("2023/Lumen Pay - Onboarding Funnel Optimization/Onboarding Recommendations.docx").then((d) => extractText(d, "Onboarding Recommendations.docx", BIG)),
      read("2024/Verdant Grid - Climate-Tech Market Entry/Market Entry Assessment.pdf").then((d) => extractText(d, "Market Entry Assessment.pdf", BIG)),
      read("2022/Northwind Grocers - Loyalty Pricing Review/Price Elasticity Model.xlsx").then((d) => extractText(d, "Price Elasticity Model.xlsx", BIG)),
    ]);
    expect(pptx.text).toContain("champion enablement kit");
    expect(docx.text).toMatch(/selfie-verification fallback/i);
    expect(docx.text).toContain("Identity verification");
    expect(pdf.text).toMatch(/interconnection\s+queue/);
    expect(xlsx.text).toContain("Bananas (lb)");
    expect([pptx, docx, pdf, xlsx].every((r) => !r.truncated && r.totalChars === r.text.length)).toBe(true);
  }, 60_000);

  it("normalizes a huge run of spaces in linear time", async () => {
    const hostile = "a" + " ".repeat(4_000_000) + "b\n" + " ".repeat(4_000_000) + "c";
    const started = performance.now();
    const r = await extractText(Buffer.from(hostile), "spaces.txt", 50_000_000);
    expect(performance.now() - started).toBeLessThan(1_500);
    expect(r.totalChars).toBe(hostile.length);
    expect(r.text).toBe(hostile); // interior runs are left alone; only trailing blanks at line ends are stripped
    // Trailing spaces/tabs are stripped from lines that end in them.
    expect((await extractText(Buffer.from("x   \ny\t\t\nz"), "t.txt", BIG)).text).toBe("x\ny\nz");
    expect((await extractText(Buffer.from("a" + " ".repeat(3_000_000) + "\nb"), "t.txt", BIG)).text).toBe("a\nb");
  });
});
