// Generated image for transport/UI checks, not a real weld or accuracy benchmark.
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) { crc ^= b; for (let i=0;i<8;i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(name, data) {
  const type = Buffer.from(name), length = Buffer.alloc(4), crc = Buffer.alloc(4);
  length.writeUInt32BE(data.length); crc.writeUInt32BE(crc32(Buffer.concat([type,data])));
  return Buffer.concat([length,type,data,crc]);
}
const width = 640, height = 480, pixels = Buffer.alloc((width*3+1)*height);
for (let y=0;y<height;y++) for (let x=0;x<width;x++) {
  const band = x > 270 && x < 370;
  const curve = band && Math.abs((y % 25) - 10 - 7*Math.cos((x-320)/50*Math.PI)) < 2;
  const value = curve ? 85 : band ? 170 : 136;
  const offset = y*(width*3+1)+1+x*3; pixels.fill(value,offset,offset+3);
}
const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width,0); ihdr.writeUInt32BE(height,4); ihdr[8]=8; ihdr[9]=2;
fs.writeFileSync(path.join(__dirname,'synthetic-test.png'),Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',ihdr),chunk('IDAT',zlib.deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))]));
