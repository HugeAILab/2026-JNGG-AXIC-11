const assert = require('node:assert/strict');
const {decodeCocoRle} = require('../app/static/js/segmentation.js');
function encode(runs) {
  let text = '';
  runs.forEach((run, index) => {
    let value = index > 2 ? run - runs[index - 2] : run;
    let more;
    do {
      let c = value & 31;
      value >>= 5;
      more = c & 16 ? value !== -1 : value !== 0;
      if (more) c |= 32;
      text += String.fromCharCode(c + 48);
    } while (more);
  });
  return text;
}
assert.deepEqual(decodeCocoRle('04', 4), [0, 4]);
for (const runs of [[2, 2], [0, 50, 3, 2, 8, 100], [500, 1000, 8, 2, 3, 5], [4]]) {
  assert.deepEqual(decodeCocoRle(encode(runs), runs.reduce((a,b) => a+b, 0)), runs);
}
assert.throws(() => decodeCocoRle('04', 3));
assert.throws(() => decodeCocoRle('P', 4));
console.log('RLE: known masks, signed deltas, multibyte runs, invalid lengths passed');
