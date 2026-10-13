import assert from 'node:assert/strict';
import { readExamClock, examSecondsLeft } from '../src/features/studio/examClock.ts';
const now = 1_000_000;
const fresh = readExamClock(null, 3, now);
assert.equal(examSecondsLeft(fresh, now), 360);
const reopened = readExamClock(JSON.stringify(fresh), 3, now + 75_000);
assert.equal(examSecondsLeft(reopened, now + 75_000), 285);
assert.equal(reopened.deadline, fresh.deadline);
assert.equal(examSecondsLeft(reopened, now + 400_000), 0);
assert.equal(examSecondsLeft(reopened, now + 120_900), 240);
assert.equal(readExamClock(JSON.stringify({...fresh,completed:true}),3,now).completed,true);
for (const bad of ['broken', '{"count":3,"deadline":"bad","completed":false}', '{"count":3,"deadline":-1,"completed":false}']) {
 assert.equal(examSecondsLeft(readExamClock(bad, 3, now),now),360);
}
assert.equal(examSecondsLeft(readExamClock(JSON.stringify(fresh),2,now),now),240);
console.log('EXAM CLOCK VERIFIED: resume, reload, elapsed time, expiry, completed state and invalid storage');
