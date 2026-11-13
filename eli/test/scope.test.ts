import { describe, expect, it } from "vitest";
import { InvalidPathError, NotAllowedError } from "../src/sharepoint/errors.js";
import { Scope, normalizePath } from "../src/sharepoint/scope.js";

describe("normalizePath", () => {
  it("trims and collapses separators", () => {
    expect(normalizePath("/Past Projects//2023/")).toBe("Past Projects/2023");
    expect(normalizePath("Past Projects\\2023")).toBe("Past Projects/2023");
    expect(normalizePath("")).toBe("");
    expect(normalizePath("/")).toBe("");
  });

  it.each([
    "..",
    "../x",
    "Past Projects/../Secret",
    "Past Projects/./x",
    "Past Projects/%2e%2e/Secret",
    "Past Projects/%2E%2E/Secret",
    "Past Projects/%252e%252e/Secret",
    "Past Projects/ .. /Secret",
    "Past Projects/.../Secret",
    "Past Projects\\..\\Secret",
  ])("rejects traversal: %s", (p) => {
    expect(() => normalizePath(p)).toThrow(InvalidPathError);
  });

  it.each(["a\u0000b", "a\nb", "a:b", "a*b", "a?b", 'a"b', "a<b", "a|b"])("rejects illegal chars: %j", (p) => {
    expect(() => normalizePath(p)).toThrow(InvalidPathError);
  });

  it.each(["WorK", "Å", "Ω", "Past Projects/Kelvin"])(
    "rejects code points that NFC silently rewrites into a different character: %j",
    (p) => {
      expect(() => normalizePath(p)).toThrow(InvalidPathError);
    },
  );

  it("still accepts ordinary decomposed and precomposed accents", () => {
    expect(normalizePath("Études")).toBe("Études");
    expect(normalizePath("Études")).toBe("Études");
  });

  it("treats a literal percent sequence that is not a dot segment as a normal name", () => {
    expect(normalizePath("Past Projects/100%25 growth")).toBe("Past Projects/100%25 growth");
  });
});

describe("Scope", () => {
  const scope = new Scope(["Past Projects", "/Pitches/2024/"]);

  it("allows roots and descendants, case-insensitively", () => {
    expect(scope.isAllowed("Past Projects")).toBe(true);
    expect(scope.isAllowed("past projects/2023/Deck.pptx")).toBe(true);
    expect(scope.isAllowed("Pitches/2024/x.docx")).toBe(true);
  });

  it("matches on folder boundaries only", () => {
    expect(scope.isAllowed("Past Projects 2")).toBe(false);
    expect(scope.isAllowed("Past Projects-Confidential/x.pptx")).toBe(false);
    expect(scope.isAllowed("Pitches/2024x/x.docx")).toBe(false);
  });

  it("denies siblings, ancestors and the library root", () => {
    expect(scope.isAllowed("Client Confidential/x.pptx")).toBe(false);
    expect(scope.isAllowed("Pitches")).toBe(false);
    expect(scope.isAllowed("")).toBe(false);
  });

  it("never throws from isAllowed and denies invalid paths", () => {
    expect(scope.isAllowed("Past Projects/../Client Confidential/x")).toBe(false);
    expect(scope.isAllowed("Past Projects/%2e%2e/x")).toBe(false);
    expect(scope.isAllowed("a\u0000b")).toBe(false);
  });

  it("assertAllowed distinguishes invalid from out-of-scope", () => {
    expect(scope.assertAllowed("/Past Projects/2023/")).toBe("Past Projects/2023");
    expect(() => scope.assertAllowed("Client Confidential")).toThrow(NotAllowedError);
    expect(() => scope.assertAllowed("Past Projects/../x")).toThrow(InvalidPathError);
  });

  it("fails closed on bad construction", () => {
    expect(() => new Scope([])).toThrow();
    expect(() => new Scope([""])).toThrow(/whole library/);
    expect(() => new Scope(["/"])).toThrow(/whole library/);
    expect(() => new Scope(["Past Projects/.."])).toThrow(InvalidPathError);
  });

  it("does not let Unicode case-folding collisions pass for an allowed root", () => {
    const work = new Scope(["Work"]);
    expect(work.isAllowed("Work/x")).toBe(true);
    expect(work.isAllowed("wORK/x")).toBe(true);
    expect(work.isAllowed("WorK/x")).toBe(false); // KELVIN SIGN folds to "k" under toLowerCase()
    expect(work.isAllowed("WorK")).toBe(false);
    expect(new Scope(["Fil"]).isAllowed("Fİl/x")).toBe(false); // dotted capital I
  });

  it("requires non-ASCII letters to match exactly (fails closed)", () => {
    const s = new Scope(["Études"]); // "Études"
    expect(s.isAllowed("Études/x")).toBe(true);
    expect(s.isAllowed("études/x")).toBe(false);
    // Decomposed input ("E" + combining acute) is normalized to NFC first, so it matches the same root.
    expect(s.isAllowed("Études/x")).toBe(true);
  });

  it("de-duplicates roots case-insensitively", () => {
    expect(new Scope(["Past Projects", "past projects/"]).roots).toHaveLength(1);
  });
});
