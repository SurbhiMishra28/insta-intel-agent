#!/usr/bin/env node
/**
 * Generates simple PWA icons as PNG files.
 * Uses a minimal canvas-based approach — no external deps needed.
 * Icons: 192x192 (standard) and 512x512 (maskable).
 */
const { createCanvas, loadImage } = require('canvas');
const fs = require('fs');
const path = require('path');

const OUT = path.join(__dirname, 'public', 'icons');
if (!fs.existsSync(OUT)) fs.mkdirSync(OUT, { recursive: true });

// Brand colors
const BG = '#7c5cff';   // purple (matches the app's carousel color)
const FG = '#ffffff';

function drawIcon(size, maskable = false) {
  const canvas = createCanvas(size, size);
  const ctx = canvas.getContext('2d');

  // Background
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, size, size);

  if (maskable) {
    // Safe zone: leave 10% padding on all sides
    const pad = size * 0.1;
    ctx.fillStyle = BG;
    ctx.clearRect(0, 0, size, size);
    ctx.fillRect(pad, pad, size - pad * 2, size - pad * 2);
    ctx.fillStyle = BG;
    ctx.fillRect(pad, pad, size - pad * 2, size - pad * 2);
  }

  // Draw a stylized "II" monogram (Instagram intelligence)
  const cx = size / 2;
  const fontSize = Math.round(size * 0.45);
  ctx.font = `bold ${fontSize}px system-ui, -apple-system, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = FG;

  // Two vertical bars with gap
  const barW = fontSize * 0.18;
  const barH = fontSize * 0.65;
  const gap = fontSize * 0.22;
  const totalW = barW * 2 + gap;
  const x0 = cx - totalW / 2;

  ctx.fillRect(x0, cx - barH / 2, barW, barH);
  ctx.fillRect(x0 + barW + gap, cx - barH / 2, barW, barH);

  // Small dot accent
  ctx.beginPath();
  ctx.arc(cx, cx + barH / 2 + fontSize * 0.12, fontSize * 0.06, 0, Math.PI * 2);
  ctx.fill();

  return canvas;
}

function save(canvas, name) {
  const buf = canvas.toBuffer('image/png');
  const out = path.join(OUT, name);
  fs.writeFileSync(out, buf);
  console.log(`  created ${out} (${canvas.width}x${canvas.height})`);
}

console.log('Generating PWA icons...');
[drawIcon(192, false), drawIcon(512, true)].forEach((c, i) => {
  save(c, i === 0 ? 'icon-192.png' : 'icon-512-maskable.png');
});
console.log('Done.');
