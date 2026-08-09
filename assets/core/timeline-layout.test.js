const test = require('node:test');
const assert = require('node:assert/strict');
const layout = require('./timeline-layout.js');
const dates = require('./timeline-interactions.js');

const day = value => `2026-01-${String(value).padStart(2, '0')}`;
const task = (id, first, last = first, extra = {}) => ({id, start: first ? day(first) : '', due: last ? day(last) : '', group: 'a', parent: '', ...extra});
const milestone = (id, taskId, value, extra = {}) => ({id, task_id: taskId, date: day(value), ...extra});
const lane = (result, id) => result.positions.get(String(id)).lane;

test('explicit tree drop reparents a subtree, detaches to Group root and adopts an anchor parent without changing dates', () => {
  const tasks = [task(1, 1, 4), task(2, 2, 3, {parent: 1}), task(3, 5, 6, {parent: 2}), task(4, 8, 9)];
  const original = JSON.stringify(tasks);
  const nested = layout.reparent(tasks, 2, 4);
  assert.deepEqual(layout.hierarchy(nested).map(item => item.id), [1, 4, 2, 3]);
  assert.equal(nested.find(item => item.id === 2).parent, 4);
  assert.equal(nested.find(item => item.id === 3).parent, 2);
  assert.equal(nested.find(item => item.id === 2).start, day(2));
  const detached = layout.reparent(nested, 2);
  assert.deepEqual(layout.hierarchy(detached).map(item => item.id), [1, 4, 2, 3]);
  assert.equal(detached.find(item => item.id === 2).parent, '');
  const sibling = layout.reparent(tasks, 4, 1, 2, 'before');
  assert.deepEqual(layout.hierarchy(sibling).map(item => item.id), [1, 4, 2, 3]);
  assert.equal(sibling.find(item => item.id === 4).parent, 1);
  assert.equal(JSON.stringify(tasks), original, 'Preview never mutates source metadata');
});

test('tree preview rejects self/descendant cycles, another Group, missing targets and inconsistent sibling parent', () => {
  const tasks = [task(1, 1), task(2, 2, 2, {parent: 1}), task(3, 3, 3, {parent: 2}), task(4, 4, 4, {group: 'other'})];
  for (const args of [[1, 1], [1, 3], [2, 3], [2, 4], [2, 99], [2, '', 3], [2, '', 1, 'inside']]) {
    assert.equal(layout.reparent(tasks, ...args), null);
  }
});

test('compact packing keeps canonical order and joins the nearest preceding available row', () => {
  const tasks = [task(1, 1, 5), task(2, 3, 7), task(3, 8, 10)];
  const result = layout.pack(tasks, [], 42);
  assert.equal(result.count, 2);
  assert.deepEqual(result.order, ['1', '2', '3']);
  assert.equal(lane(result, 1), 0); assert.equal(lane(result, 2), 1); assert.equal(lane(result, 3), 1);
  const moved = layout.reorder(tasks, 3, 1, 'after');
  const packedMoved = layout.pack(moved, [], 42);
  assert.deepEqual(packedMoved.order, ['1', '3', '2']);
  assert.equal(lane(packedMoved, 1), lane(packedMoved, 3));
  assert.notEqual(lane(packedMoved, 1), lane(packedMoved, 2));
  assert.deepEqual(layout.pack(moved, [], 42, true).order, ['1', '3', '2']);

  const groups = [[task(4, 1, 5, {group: 'a'})], [task(5, 1, 5, {group: 'b'})]];
  groups.forEach(items => {
    const packed = layout.pack(items, [], 42);
    assert.equal(packed.count, 1); assert.equal(lane(packed, items[0].id), 0);
  });
});

test('nearest preceding means the previous canonical Task lane, not the largest or smallest lane index', () => {
  const tasks = [task(1, 1, 5), task(2, 3, 7), task(3, 4, 8), task(4, 7, 9), task(5, 12, 13)];
  const result = layout.pack(tasks, [], 42);
  assert.deepEqual(result.order, ['1', '2', '3', '4', '5']);
  assert.deepEqual(tasks.map(item => lane(result, item.id)), [0, 1, 2, 0, 0]);
  assert.equal(lane(result, 5), lane(result, 4),
    'Although lane 2 is also free, the nearest prior Task 4 occupies lane 0');
});

test('manual reorder moves whole subtrees and rejects changes of Group or actual sibling parent', () => {
  const tasks = [
    task(1, 10, 11), task(2, 1, 2, {parent: 1}), task(3, 3, 4, {parent: 2}),
    task(4, 5, 6, {parent: 1}), task(5, 20, 21),
  ];
  const reordered = layout.reorder(tasks, 1, 5, 'after');
  assert.deepEqual(reordered.map(item => item.id), [5, 1, 2, 3, 4]);
  assert.deepEqual(reordered.map(item => [item.id, item.parent, item.group, item.start, item.due]).sort((a, b) => a[0] - b[0]),
    tasks.map(item => [item.id, item.parent, item.group, item.start, item.due]));
  assert.equal(layout.reorder(tasks, 1, 3, 'after'), null, 'A parent cannot move inside its descendant block');
  assert.equal(layout.reorder(tasks, 2, 5, 'after'), null, 'Reorder does not reparent a child to a root');
  assert.equal(layout.reorder([task(1, 1), task(2, 2, 2, {group: 'b'})], 1, 2, 'after'), null);
  const filtered = [task(2, 1, 2, {parent: 999, order_parent: 999}), task(5, 3, 4, {order_parent: ''})];
  assert.equal(layout.reorder(filtered, 2, 5, 'after'), null,
    'A filtered-out parent remains a real sibling constraint even if the visible tree renders an orphan at root depth');
});

test('parent collapse hides descendant lanes and their marker footprint without losing branch information', () => {
  const tasks = [task(1, 1), task(2, 2, 2, {parent: 1}), task(3, 3, 3, {parent: 2}), task(4, 10)];
  const markers = [milestone(1, 3, 10)];
  const open = layout.pack(tasks, markers, 42);
  assert.equal(open.count, 2, 'The grandchild marker blocks Task 4 while the subtree is shown');
  const closed = layout.pack(tasks, markers, 42, false, new Set(['1']));
  assert.deepEqual(closed.order, ['1', '4']);
  assert.equal(closed.count, 1);
  assert.equal(closed.positions.has('2'), false); assert.equal(closed.positions.has('3'), false);
  assert.equal(closed.positions.get('1').hasChildren, true, 'The expand arrow remains available');
  const expanded = layout.pack(tasks, markers, 42, true);
  assert.deepEqual(expanded.order, ['1', '2', '3', '4']);
  assert.equal(expanded.count, 4);
  assert.deepEqual(expanded.positions.get('3').ancestors, ['1', '2']);
  assert.equal(expanded.positions.get('2').hasChildren, true);
  assert.equal(expanded.positions.get('3').hasChildren, false);
  assert.ok(expanded.positions.get('3').branchLast.length > expanded.positions.get('2').branchLast.length);
});

test('bundle footprints are unions: a distant flag does not reserve the empty gap', () => {
  for (const width of [16, 42, 72]) {
    const tasks = [task(1, 1, 3), task(2, 5, 7), task(3, 10, 12)];
    const markers = [milestone(1, 1, 10)];
    const footprint = layout.taskFootprint(tasks[0], layout.clusters(markers), width);
    assert.equal(footprint.length, 2, 'Keep the free interval between the bar and its distant flag');
    const result = layout.pack(tasks, markers, width);
    assert.equal(lane(result, 1), lane(result, 2), `The gap remains usable at ${width}px/day`);
    assert.notEqual(lane(result, 1), lane(result, 3), `A flag/bar collision needs another lane at ${width}px/day`);
    assert.equal(result.count, 2);
  }
});

test('nearest preceding Task chooses its row even after that Task reused an earlier lane', () => {
  const result = layout.pack([task(1, 1, 6), task(2, 2, 7), task(3, 3, 8), task(4, 7, 8), task(5, 10, 11)], [], 42);
  assert.deepEqual([1, 2, 3, 4, 5].map(id => lane(result, id)), [0, 1, 2, 0, 0]);
});

test('manual reorder moves true subtrees, rejects cross-parent targets and appends unranked Tasks', () => {
  const tasks = [task(1, 10, 12, {position: 0}), task(2, 1, 2, {parent: 1, position: 1}), task(3, 15, 16, {position: 2}), task(4, 20, 21, {position: 3}), task(5, 22, 23, {parent: 4, position: 4})];
  const moved = layout.reorder(tasks, 1, 4, 'after');
  assert.deepEqual(layout.hierarchy(moved).map(item => item.id), [3, 4, 5, 1, 2]);
  assert.equal(layout.reorder(tasks, 2, 3, 'after'), null);
  assert.equal(layout.reorder(tasks, 1, 2, 'after'), null);
  assert.deepEqual(layout.hierarchy([task(9, 1), ...tasks]).map(item => item.id), [1, 2, 3, 4, 5, 9]);
  assert.deepEqual(tasks.map(item => item.position), [0, 1, 2, 3, 4], 'Preview must not mutate authoritative positions');
});

test('true tree branches and collapsed ancestors hide descendants without changing canonical order', () => {
  const tasks = [task(1, 1), task(2, 2, 2, {parent: 1}), task(3, 3, 3, {parent: 2}), task(4, 4, 4, {parent: 1}), task(5, 5)];
  const tree = layout.hierarchy(tasks);
  assert.deepEqual(tree.map(item => [item.id, item.depth, item.hasChildren]), [[1, 0, true], [2, 1, true], [3, 2, false], [4, 1, false], [5, 0, false]]);
  assert.deepEqual(tree.find(item => item.id === 3).branchLast, [false, false, true]);
  assert.deepEqual(tree.find(item => item.id === 4).branchLast, [false, true]);
  assert.deepEqual(layout.pack(tasks, [], 42, true, new Set(['1'])).order, ['1', '5']);
  assert.equal(layout.pack(tasks, [], 42, true, new Set(['1'])).count, 2);
  assert.deepEqual(layout.pack(tasks, [], 42, true).order, ['1', '2', '3', '4', '5']);
});

test('same-day flags cluster only by their actual Task, Group or global owner', () => {
  const markers = [
    milestone(1, 7, 3, {group_id: 4}), milestone(2, 7, 3, {group_id: 4}),
    milestone(3, 8, 3, {group_id: 4}),
    {id: 4, group_id: 4, date: day(3)}, {id: 5, timeline_group: 4, date: day(3)},
    {id: 6, date: day(3)}, {id: 7, task: 7, date: day(4)},
    milestone(8, 9, 3, {task_has_schedule: false, task_group: 4}),
  ];
  const clusters = layout.clusters(markers);
  assert.equal(clusters.length, 6);
  const sizes = Object.fromEntries(clusters.map(cluster => [cluster.key, cluster.items.length]));
  assert.equal(sizes[`task:7:${day(3)}`], 2);
  assert.equal(sizes[`task:8:${day(3)}`], 1);
  assert.equal(sizes[`group:4:${day(3)}`], 2);
  assert.equal(sizes[`global:${day(3)}`], 1);
  assert.equal(sizes[`task:7:${day(4)}`], 1);
  assert.equal(sizes[`task:9:${day(3)}`], 1, 'An unscheduled Task still owns its markers');
  assert.equal(layout.owner({task: 7, timeline_group: 4}), 'task:7');
});

test('saved positions, not dates or count badges, control canonical order across zoom', () => {
  const tasks = [task(2, 1), task(1, 10), task(3, 1), task(4, 20)];
  const markers = Array.from({length: 20}, (_, index) => milestone(index + 1, 1, 1));
  markers.push(milestone(21, 4, 2));
  assert.equal(layout.clusters(markers).find(cluster => cluster.owner === 'task:1').items.length, 20);
  assert.ok(layout.markerWidth(20) > layout.markerWidth(2));
  assert.ok(layout.markerWidth(2) > layout.markerWidth(1));
  for (const width of [16, 42, 72]) {
    assert.deepEqual(layout.pack(tasks, markers, width).order, ['2', '1', '3', '4'],
      'Dates and badges must never sort a Task ahead of the canonical order');
    assert.deepEqual(layout.pack(tasks.map((item, index) => ({...item, position: 3 - index})), markers, width).order, ['4', '3', '1', '2']);
  }
});

test('all marker dates affect packing even when a marker is outside the rendered date slice', () => {
  const tasks = [
    {id: 1, start: '2026-01-01', due: '2026-01-03'},
    {id: 2, start: '2028-01-10', due: '2028-01-12'},
    {id: 3, start: '2027-01-01', due: '2027-01-03'},
  ];
  const markers = [{id: 1, task_id: 1, date: '2028-01-10'}];
  const result = layout.pack(tasks, markers, 42);
  assert.deepEqual(result.order, ['1', '2', '3']);
  assert.equal(result.count, 2);
  assert.equal(lane(result, 2), lane(result, 3), 'Nearest preceding available Task controls the shared row');
  assert.notEqual(lane(result, 1), lane(result, 2));
  // There is deliberately no viewport parameter: panning cannot change these positions.
  assert.deepEqual([...layout.pack(tasks, markers, 42).positions], [...result.positions]);
});

test('adjacent inclusive bars fit together, shared-day bars and marker collisions do not', () => {
  assert.equal(layout.overlaps([[1, 3], [8, 10]], [[3, 8]]), false);
  assert.equal(layout.overlaps([[1, 3], [8, 10]], [[2, 4]]), true);
  assert.equal(layout.overlaps([], [[2, 4]]), false);
  assert.equal(layout.pack([task(1, 1, 3), task(2, 4, 6)], [], 42).count, 1);
  assert.equal(layout.pack([task(1, 1, 3), task(2, 3, 6)], [], 42).count, 2);
  const markers = [milestone(1, 1, 10), milestone(2, 2, 10)];
  assert.equal(layout.pack([task(1, 1, 3), task(2, 5, 7)], markers, 42).count, 2);
});

test('marker badge width is included in collision detection at small day widths', () => {
  const tasks = [task(1, 1), task(2, 9)];
  const markers = Array.from({length: 20}, (_, index) => milestone(index, 1, 10));
  assert.equal(layout.pack(tasks, markers, 16).count, 2, 'The 20-count badge extends into day 9 at 16px/day');
  assert.equal(layout.pack(tasks, markers, 72).count, 1, 'At 72px/day the badge fits inside day 10');
});

test('consecutive count badges reserve their displaced positions and leader lines before packing', () => {
  const tasks = [task(1, 1), task(2, 14)];
  const markers = Array.from({length: 40}, (_, index) => milestone(index + 1, 1, index < 20 ? 10 : 11));
  const clusters = layout.clusters(markers);
  const placed = layout.positionClusters(clusters, 16);
  assert.equal(placed.length, 2);
  assert.ok(placed[1].left > placed[1].anchor - layout.markerWidth(20) / 2,
    'The second consecutive badge is shifted away from its original day to remain clickable');
  assert.ok(placed[0].right + 3 <= placed[1].left);
  const firstFootprint = layout.taskFootprint(tasks[0], clusters, 16);
  assert.equal(layout.overlaps(firstFootprint, layout.taskFootprint(tasks[1], [], 16)), true,
    'Although the original anchors are earlier, the shifted second badge reaches Task 2');
  assert.ok(firstFootprint.some(([left, right]) => left <= placed[1].anchor && right >= placed[1].right + 4),
    'Both the badge and its leader line are part of the owner Task footprint');
  assert.notEqual(lane(layout.pack(tasks, markers, 16), 1), lane(layout.pack(tasks, markers, 16), 2));
  assert.equal(layout.pack(tasks, markers, 72).count, 1,
    'With sufficient day width the undisplaced badges do not unnecessarily reserve Task 2\'s dates');
});

test('hierarchy preserves every Task with cycles, missing parents, self parents and cross-Group parents', () => {
  const tasks = [
    task(2, 1, 1, {parent: 1}), task(1, 2),
    task(3, 3, 3, {parent: 999}), task(4, 4, 4, {parent: 1, group: 'b'}),
    task(5, 5, 5, {parent: 5}), task(6, 6, 6, {parent: 7}), task(7, 7, 7, {parent: 6}),
  ];
  const result = layout.hierarchy(tasks);
  assert.deepEqual(result.map(item => item.id), [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(new Set(result.map(item => item.id)).size, tasks.length);
  const depths = Object.fromEntries(result.map(item => [item.id, item.depth]));
  assert.equal(depths[1], 0); assert.equal(depths[2], 1);
  for (const id of [3, 4, 5]) assert.equal(depths[id], 0);
  assert.equal(depths[6], 0); assert.equal(depths[7], 1);
  const deep = Array.from({length: 20}, (_, index) => task(index + 1, 1, 1, {parent: index || ''}));
  assert.equal(Math.max(...layout.hierarchy(deep).map(item => item.depth)), 12);
});

test('expanded mode gives one lane per Task and follows the Group hierarchy, not date sorting', () => {
  const tasks = [task(2, 1, 2, {parent: 1}), task(1, 10, 12), task(3, 15, 16)];
  const result = layout.pack(tasks, [], 42, true);
  assert.deepEqual(result.order, ['1', '2', '3']);
  assert.equal(result.count, 3);
  assert.equal(lane(result, 1), 0); assert.equal(lane(result, 2), 1); assert.equal(lane(result, 3), 2);
  assert.equal(result.positions.get('2').depth, 1);
});

test('footprints normalize missing endpoints without mutating source data; empty groups stay empty', () => {
  const dueOnly = task(1, 0, 5);
  const startOnly = task(2, 8, 0);
  const before = JSON.stringify([dueOnly, startOnly]);
  assert.deepEqual(layout.taskFootprint(dueOnly, [], 42), [[dates.ordinal(day(5)) * 42, dates.ordinal(day(6)) * 42]]);
  assert.deepEqual(layout.taskFootprint(startOnly, [], 42), [[dates.ordinal(day(8)) * 42, dates.ordinal(day(9)) * 42]]);
  layout.pack([dueOnly, startOnly], [], 42);
  assert.equal(JSON.stringify([dueOnly, startOnly]), before);
  assert.equal(layout.pack([], [], 42).count, 0);
  assert.deepEqual(layout.pack([], [], 42).order, []);
});

test('logical extent is a monotonic union; repeated inner requests never shrink retained years', () => {
  let extent = {start: '2026-01-01', end: '2026-12-31'};
  for (const requested of [
    ['2028-01-01', '2028-12-31'], ['2027-03-01', '2027-04-01'],
    ['2025-01-01', '2025-12-31'], ['2026-05-01', '2026-06-01'],
  ]) {
    const next = layout.unionExtent(extent.start, extent.end, ...requested);
    assert.ok(next.start <= extent.start); assert.ok(next.end >= extent.end); extent = next;
  }
  assert.deepEqual(extent, {start: '2025-01-01', end: '2028-12-31'});
  assert.deepEqual(layout.unionExtent(extent.start, extent.end, '0001-01-01', '9999-12-31'), {start: '0001-01-01', end: '9999-12-31'});
  assert.deepEqual(layout.unionExtent('0001-01-01', '9999-12-31', ...Object.values(extent)), {start: '0001-01-01', end: '9999-12-31'});
  assert.deepEqual(layout.unionExtent('2026-01-01', '2026-12-31', '0000-12-31', '2027-01-01'), {start: '0001-01-01', end: '2027-01-01'});
  assert.throws(() => layout.unionExtent('2026-01-01', '2026-12-31', 'invalid', '2027-01-01'));
});

test('annual February 29 holidays fall back to February 28 while preserving inclusive duration', () => {
  const holiday = {id: 1, start_date: '2024-02-29', end_date: '2024-03-02', repeat_annually: true};
  for (const date of ['2025-02-28', '2025-03-01', '2025-03-02', '2028-02-29', '2028-03-01', '2028-03-02']) {
    assert.deepEqual(layout.holidayOn(date, [holiday]), [holiday], date);
  }
  for (const date of ['2025-02-27', '2025-03-03', '2028-02-28', '2028-03-03']) assert.deepEqual(layout.holidayOn(date, [holiday]), [], date);
});

test('annual cross-year holidays include the prior-year occurrence once; one-off dates do not repeat', () => {
  const annual = {id: 1, start_date: '2024-12-30', end_date: '2025-01-02', repeat_annually: true};
  const oneOff = {id: 2, start_date: '2026-01-01', end_date: '2026-01-01', repeat_annually: false};
  for (const date of ['2026-01-01', '2026-01-02', '2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02']) {
    assert.equal(layout.holidayOn(date, [annual]).filter(item => item.id === 1).length, 1, date);
  }
  for (const date of ['2026-01-03', '2026-12-29', '2027-01-03']) assert.deepEqual(layout.holidayOn(date, [annual]), [], date);
  assert.deepEqual(layout.holidayOn('2026-01-01', [annual, oneOff]), [annual, oneOff]);
  assert.deepEqual(layout.holidayOn('2027-01-01', [annual, oneOff]), [annual]);
  assert.deepEqual(layout.holidayOn('0001-01-01', [annual]), [], 'No year-zero occurrence is constructed');
});
