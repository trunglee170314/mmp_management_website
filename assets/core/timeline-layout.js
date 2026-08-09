/* Geometry uses absolute dates, never clipped viewport dates. No DOM or private data. */
((root, factory) => {
  const api = factory(typeof module === 'object' && module.exports ? require('./timeline-interactions.js') : root.MMPTimeline);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MMPTimelineLayout = api;
})(typeof globalThis === 'object' ? globalThis : this, dates => {
  const minimum = dates.ordinal('0001-01-01'), maximum = dates.ordinal('9999-12-31');
  const owner = item => item.task_id || item.task ? `task:${item.task_id || item.task}` : item.group_id || item.timeline_group ? `group:${item.group_id || item.timeline_group}` : 'global';
  const clusters = items => {
    const result = new Map();
    items.forEach(item => {
      const key = `${owner(item)}:${item.date}`;
      if (!result.has(key)) result.set(key, {key, owner: owner(item), date: item.date, items: []});
      result.get(key).items.push(item);
    });
    return [...result.values()].sort((a, b) => a.date.localeCompare(b.date) || a.key.localeCompare(b.key));
  };
  const markerWidth = size => size > 1 ? 29 + String(size).length * 7 : 22;
  const positionClusters = (markers, width) => {
    let right = -Infinity;
    return markers.slice().sort((a, b) => a.date.localeCompare(b.date) || a.key.localeCompare(b.key)).map(marker => {
      const anchor = (dates.ordinal(marker.date) + .5) * width, size = markerWidth(marker.items.length);
      const left = Math.max(anchor - size / 2, right + 3);
      right = left + size; return {...marker, anchor, left, right};
    });
  };
  const merge = intervals => {
    const result = [];
    intervals.slice().sort((a, b) => a[0] - b[0]).forEach(interval => {
      const last = result.at(-1);
      if (last && interval[0] <= last[1]) last[1] = Math.max(last[1], interval[1]);
      else result.push([...interval]);
    });
    return result;
  };
  const overlaps = (first, second) => {
    let a = 0, b = 0;
    while (a < first.length && b < second.length) {
      if (first[a][0] < second[b][1] && second[b][0] < first[a][1]) return true;
      if (first[a][1] <= second[b][0]) a++; else b++;
    }
    return false;
  };
  const taskFootprint = (task, markers, width) => {
    const start = task.start || task.due, end = task.due || task.start;
    const intervals = start && end ? [[dates.ordinal(start) * width, (dates.ordinal(end) + 1) * width]] : [];
    positionClusters(markers, width).forEach(marker => {
      intervals.push([Math.min(marker.anchor, marker.left) - 4, marker.right + 4]);
    });
    return merge(intervals);
  };
  const hierarchy = tasks => {
    tasks = tasks.map((task, rank) => ({...task, originalRank: rank})).sort((a, b) => {
      const first = a.position == null ? Infinity : Number(a.position), second = b.position == null ? Infinity : Number(b.position);
      return first - second || a.originalRank - b.originalRank;
    });
    const byId = new Map(tasks.map(task => [String(task.id), task])), children = new Map(), roots = [];
    tasks.forEach(task => {
      const parent = byId.get(String(task.parent));
      if (parent && parent.id !== task.id && String(parent.group || '') === String(task.group || '')) {
        if (!children.has(String(parent.id))) children.set(String(parent.id), []);
        children.get(String(parent.id)).push(task);
      } else roots.push(task);
    });
    const result = [], seen = new Set(), reachable = new Set();
    const mark = task => { if (reachable.has(String(task.id))) return; reachable.add(String(task.id)); (children.get(String(task.id)) || []).forEach(mark); };
    roots.forEach(mark);
    tasks.forEach(task => { if (!reachable.has(String(task.id))) { roots.push(task); mark(task); } });
    const visit = (task, ancestors, lastAncestors, isLast) => {
      if (seen.has(String(task.id))) return;
      const depth = Math.min(ancestors.length, 12);
      seen.add(String(task.id)); result.push({...task, depth, ancestors, effectiveParent: ancestors.at(-1) || '', branchLast: [...lastAncestors.slice(0, depth), isLast]});
      const descendants = children.get(String(task.id)) || [];
      descendants.forEach((child, index) => visit(child, [...ancestors, String(task.id)], [...lastAncestors, isLast], index === descendants.length - 1));
    };
    roots.forEach((task, index) => visit(task, [], [], index === roots.length - 1));
    const parentIds = new Set(result.map(task => String(task.effectiveParent)));
    return result.map(task => ({...task, hasChildren: parentIds.has(String(task.id))}));
  };
  const reorder = (tasks, sourceId, anchorId, placement) => {
    const entries = hierarchy(tasks), source = entries.find(task => String(task.id) === String(sourceId)), anchor = entries.find(task => String(task.id) === String(anchorId));
    if (!source || !anchor || source === anchor || !['before', 'after'].includes(placement)) return null;
    const parent = task => String(task.order_parent ?? task.effectiveParent ?? '');
    if (String(source.group || '') !== String(anchor.group || '') || parent(source) !== parent(anchor)) return null;
    const block = entries.filter(task => String(task.id) === String(sourceId) || task.ancestors.includes(String(sourceId)));
    if (block.some(task => String(task.id) === String(anchorId))) return null;
    const ids = new Set(block.map(task => String(task.id))), rest = entries.filter(task => !ids.has(String(task.id)));
    let index = rest.findIndex(task => String(task.id) === String(anchorId));
    if (placement === 'after') { index++; while (index < rest.length && rest[index].ancestors.includes(String(anchorId))) index++; }
    rest.splice(index, 0, ...block);
    return rest.map((task, position) => ({...task, position}));
  };
  const reparent = (tasks, sourceId, parentId = '', anchorId = '', placement = 'after') => {
    const entries = hierarchy(tasks), source = entries.find(task => String(task.id) === String(sourceId));
    const parent = parentId ? entries.find(task => String(task.id) === String(parentId)) : null;
    const anchor = anchorId ? entries.find(task => String(task.id) === String(anchorId)) : null;
    if (!source || !['before', 'after'].includes(placement) || parentId && !parent || anchorId && !anchor) return null;
    const block = entries.filter(task => String(task.id) === String(sourceId) || task.ancestors.includes(String(sourceId)));
    const ids = new Set(block.map(task => String(task.id)));
    if ([parent, anchor].some(task => task && (ids.has(String(task.id)) || String(task.group || '') !== String(source.group || '')))) return null;
    if (anchor && String(anchor.order_parent ?? anchor.effectiveParent ?? '') !== String(parentId || '')) return null;
    const rest = entries.filter(task => !ids.has(String(task.id)));
    let index = rest.length;
    const target = anchor || parent;
    if (target) {
      index = rest.findIndex(task => String(task.id) === String(target.id));
      if (!anchor || placement === 'after') { index++; while (index < rest.length && rest[index].ancestors.includes(String(target.id))) index++; }
    }
    rest.splice(index, 0, ...block.map(task => String(task.id) === String(sourceId) ? {...task, parent: parentId || '', order_parent: parentId || ''} : task));
    return rest.map((task, position) => ({...task, position}));
  };
  const pack = (tasks, milestones, width, expanded = false, collapsed = new Set()) => {
    const taskMarkers = new Map();
    clusters(milestones).forEach(marker => {
      if (!marker.owner.startsWith('task:')) return;
      if (!taskMarkers.has(marker.owner)) taskMarkers.set(marker.owner, []);
      taskMarkers.get(marker.owner).push(marker);
    });
    const entries = hierarchy(tasks).filter(task => !task.ancestors.some(id => collapsed.has(id))).map(task => {
      const markers = taskMarkers.get(`task:${task.id}`) || [];
      return {...task, intervals: taskFootprint(task, markers, width)};
    });
    const lanes = [], positions = new Map(), preceding = [];
    entries.forEach(task => {
      let lane = expanded ? lanes.length : -1;
      if (!expanded) {
        const tried = new Set();
        for (let index = preceding.length - 1; index >= 0; index--) {
          const candidate = preceding[index]; if (tried.has(candidate)) continue; tried.add(candidate);
          if (!overlaps(lanes[candidate], task.intervals)) { lane = candidate; break; }
        }
      }
      if (lane < 0) lane = lanes.length;
      lanes[lane] = merge([...(lanes[lane] || []), ...task.intervals]);
      positions.set(String(task.id), {lane, depth: task.depth, ancestors: task.ancestors, branchLast: task.branchLast, hasChildren: task.hasChildren});
      preceding.push(lane);
    });
    return {positions, count: lanes.length, order: entries.map(task => String(task.id))};
  };
  const unionExtent = (start, end, requestedStart, requestedEnd) => ({
    start: dates.date(Math.max(minimum, Math.min(dates.ordinal(start), dates.ordinal(requestedStart)))),
    end: dates.date(Math.min(maximum, Math.max(dates.ordinal(end), dates.ordinal(requestedEnd)))),
  });
  const holidayOn = (value, holidays) => {
    const day = dates.ordinal(value), year = Number(value.slice(0, 4)), matches = [];
    holidays.forEach(holiday => {
      const start = dates.ordinal(holiday.start_date), duration = dates.ordinal(holiday.end_date) - start;
      if (!holiday.repeat_annually) { if (day >= start && day <= start + duration) matches.push(holiday); return; }
      for (const candidate of [year - 1, year]) {
        if (candidate < 1) continue;
        let date = `${String(candidate).padStart(4, '0')}-${holiday.start_date.slice(5)}`, occurrence;
        try { occurrence = dates.ordinal(date); }
        catch (_) { date = `${String(candidate).padStart(4, '0')}-02-28`; occurrence = dates.ordinal(date); }
        if (day >= occurrence && day <= occurrence + duration) { matches.push(holiday); break; }
      }
    });
    return matches;
  };
  return {owner, clusters, markerWidth, positionClusters, taskFootprint, overlaps, hierarchy, reorder, reparent, pack, unionExtent, holidayOn};
});
