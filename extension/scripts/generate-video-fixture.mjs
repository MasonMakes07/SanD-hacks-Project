// Creates an original synthetic black 16x16 VP8/WebM timeline for native media
// capture tests. Browser WebP supplies VP8 keyframes; no external media/codecs.
import { chromium } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
const browser = await chromium.launch();
let webp;
try {
  const page = await browser.newPage();
  webp = Buffer.from(await page.evaluate(() => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 16;
    canvas.getContext('2d').fillRect(0, 0, 16, 16);
    return canvas.toDataURL('image/webp').split(',')[1];
  }), 'base64');
} finally { await browser.close(); }
let vp8;
for (let offset = 12; offset < webp.length;) {
  const size = webp.readUInt32LE(offset + 4);
  if (webp.toString('ascii', offset, offset + 4) === 'VP8 ') vp8 = webp.subarray(offset + 8, offset + 8 + size);
  offset += 8 + size + (size % 2);
}
if (!vp8) throw new Error('Browser did not encode a VP8 WebP');
const uint = value => { let hex = value.toString(16); if (hex.length % 2) hex = `0${hex}`; return Buffer.from(hex, 'hex'); };
const size = value => { for (let n = 1; n <= 8; n++) if (value < 2 ** (7 * n) - 1) { const out = Buffer.alloc(n); let v = BigInt(value) | (1n << BigInt(7 * n)); for (let i = n - 1; i >= 0; i--) { out[i] = Number(v & 255n); v >>= 8n; } return out; } throw new Error('Oversized fixture'); };
const element = (id, value) => {
  const body = Array.isArray(value) ? Buffer.concat(value) : typeof value === 'string' ? Buffer.from(value) : typeof value === 'number' ? uint(value) : value;
  return Buffer.concat([uint(id), size(body.length), body]);
};
const duration = Buffer.alloc(8); duration.writeDoubleBE(205000);
const header = element(0x1a45dfa3, [element(0x4286, 1), element(0x42f7, 1), element(0x42f2, 4), element(0x42f3, 8), element(0x4282, 'webm'), element(0x4287, 2), element(0x4285, 2)]);
const info = element(0x1549a966, [element(0x2ad7b1, 1000000), element(0x4d80, 'TruthTrace fixture'), element(0x5741, 'TruthTrace fixture'), element(0x4489, duration)]);
const tracks = element(0x1654ae6b, [element(0xae, [element(0xd7, 1), element(0x73c5, 1), element(0x83, 1), element(0x86, 'V_VP8'), element(0xe0, [element(0xb0, 16), element(0xba, 16)])])]);
const clusters = Array.from({ length: 205 }, (_, index) => element(0x1f43b675, [element(0xe7, index * 1000), element(0xa3, Buffer.concat([Buffer.from([0x81, 0, 0, 0x80]), vp8]))]));
await writeFile(new URL('../tests/fixtures/video-moment.webm', import.meta.url), Buffer.concat([header, element(0x18538067, [info, tracks, ...clusters])]));
console.log('Generated original 205-second synthetic native-video fixture.');
