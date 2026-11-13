import zlib from "node:zlib";

/** Minimal in-memory zip writer (deflate) so tests can build hostile office files without any CLI or dependency. */

function u16(n: number): Buffer {
  const b = Buffer.alloc(2);
  b.writeUInt16LE(n);
  return b;
}

function u32(n: number): Buffer {
  const b = Buffer.alloc(4);
  b.writeUInt32LE(n >>> 0);
  return b;
}

export function makeZip(entries: { name: string; raw: Buffer }[]): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const { name, raw } of entries) {
    const nameB = Buffer.from(name);
    const comp = zlib.deflateRawSync(raw, { level: 9 });
    const crc = zlib.crc32(raw);
    const local = Buffer.concat([
      u32(0x04034b50), u16(20), u16(0), u16(8), u16(0), u16(0x21), u32(crc), u32(comp.length), u32(raw.length),
      u16(nameB.length), u16(0), nameB, comp,
    ]);
    centrals.push(
      Buffer.concat([
        u32(0x02014b50), u16(20), u16(20), u16(0), u16(8), u16(0), u16(0x21), u32(crc), u32(comp.length), u32(raw.length),
        u16(nameB.length), u16(0), u16(0), u16(0), u16(0), u32(0), u32(offset), nameB,
      ]),
    );
    locals.push(local);
    offset += local.length;
  }
  const cd = Buffer.concat(centrals);
  return Buffer.concat([
    ...locals, cd,
    u32(0x06054b50), u16(0), u16(0), u16(entries.length), u16(entries.length), u32(cd.length), u32(offset), u16(0),
  ]);
}

/**
 * A structurally valid docx whose word/document.xml inflates to roughly `inflatedMb` megabytes of one repeated
 * paragraph. The zip itself stays tiny (about 1/280th of that), which is what makes it a decompression bomb.
 */
export function makeBombDocx(inflatedMb: number): Buffer {
  const head =
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>';
  const unit = "<w:p><w:r><w:t>bomb bomb bomb bomb bomb bomb bomb bomb</w:t></w:r></w:p>";
  const reps = Math.floor((inflatedMb * 1024 * 1024) / unit.length);
  const doc = Buffer.from(head + unit.repeat(reps) + "</w:body></w:document>");
  return makeZip([
    {
      name: "[Content_Types].xml",
      raw: Buffer.from(
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
      ),
    },
    {
      name: "_rels/.rels",
      raw: Buffer.from(
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
      ),
    },
    { name: "word/document.xml", raw: doc },
  ]);
}
