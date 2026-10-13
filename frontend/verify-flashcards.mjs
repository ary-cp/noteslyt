import assert from 'node:assert/strict';
import { advanceReview, readReview } from './src/features/studio/flashcardReview.ts';
assert.deepEqual(advanceReview([0,1,2,3,4], 'Again'), [1,0,2,3,4]);
assert.deepEqual(advanceReview([0,1,2,3,4], 'Hard'), [1,2,3,0,4]);
assert.deepEqual(advanceReview([0], 'Again'), [0]);
assert.deepEqual(advanceReview([0], 'Good'), []);
assert.deepEqual(advanceReview([0,1], 'Easy'), [1]);
assert.deepEqual(readReview(JSON.stringify({count:3,queue:[2,0]}),3), [2,0]);
assert.deepEqual(readReview(JSON.stringify({count:3,queue:[]}),3), []);
for (const raw of ['broken', '{"count":3,"queue":[0,0]}', '{"count":3,"queue":[-1]}', '{"count":2,"queue":[]}']) {
  assert.deepEqual(readReview(raw,3), [0,1,2]);
}
console.log('FLASHCARD REVIEW VERIFIED');
