const {test} = require('node:test');
const assert = require('node:assert/strict');
const S = require('../app/static/scene.js');
const center = [62.7379, 7.1608];
function approach(id, origin) { return {id, coordinates: [S.offset(center, origin, 80), center]}; }
test('right-hand traffic follows driver heading rather than map east', () => {
  for (const origin of [0, 45, 90, 135, 180, 225, 270, 315]) {
    const me = approach('me', origin);
    assert.equal(S.fromRight(me, approach('right', origin + 270)), true);
    assert.equal(S.fromRight(me, approach('left', origin + 90)), false);
    assert.equal(S.fromRight(me, approach('opposing', origin + 180)), false);
    assert.equal(S.fromRight(me, approach('same', origin)), false);
  }
});
test('ego pose follows a curved road and stays before junction', () => {
  const path = [S.offset(center, 0, 60), S.offset(center, 0, 30), center];
  const pose = S.along(path, 22, true);
  assert.ok(Math.abs(S.distance(pose.point, center) - 22) < .02);
  assert.ok(Math.abs(pose.heading - 180) < .01);
  const lane = S.offset(pose.point, pose.heading + 90, 2.2);
  assert.ok(lane[1] < pose.point[1]); // Heading south: right-hand lane is west.
});
test('short approaches clamp position to available road geometry', () => {
  const origin = S.offset(center, 90, 4);
  const pose = S.along([origin, center], 20, true);
  assert.deepEqual(pose.point, origin);
  assert.ok(Math.abs(pose.heading - 270) < .01);
});
