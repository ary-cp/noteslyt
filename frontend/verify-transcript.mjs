import assert from 'node:assert/strict';
import { transcriptSegments } from './src/features/viewer/transcript.ts';

assert.deepEqual(transcriptSegments('# Lecture\n\n## 00:00:29.760 --> 00:00:36.500\n\nGradient descent.\n\n## 00:01:02.000 --> 00:01:08.250\n\nLearning rate.\n'), [
  { start: 29.76, end: 36.5, text: 'Gradient descent.' },
  { start: 62, end: 68.25, text: 'Learning rate.' },
]);
assert.deepEqual(transcriptSegments('## 1:23\r\nLegacy transcript.\r\n\r\n## 2:05\r\nNext passage.'), [
  { start: 83, end: null, text: 'Legacy transcript.' },
  { start: 125, end: null, text: 'Next passage.' },
]);
assert.deepEqual(transcriptSegments('## p.2\nPDF page'), []);
assert.deepEqual(transcriptSegments('## 0:05 --> 0:03\nInvalid end'), [{ start: 5, end: null, text: 'Invalid end' }]);
assert.deepEqual(transcriptSegments('## 0:00\n\n## 0:03\nText'), [{ start: 3, end: null, text: 'Text' }]);
console.log('TRANSCRIPT NAVIGATION VERIFIED');
