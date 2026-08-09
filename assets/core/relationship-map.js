(() => {
  const root = document.querySelector('[data-relationship-root]');
  if (!root) return;

  const dataElement = document.getElementById('relationship-map-data');
  let graph = dataElement ? JSON.parse(dataElement.textContent) : null;
  let lastSavedGraph = graph ? structuredClone(graph) : null;
  let mutationChain = Promise.resolve();
  let mutationBusy = 0;
  const undoStack = [];
  const redoStack = [];
  const csrf = root.querySelector('[name="csrfmiddlewaretoken"]')?.value || '';
  const mapDialog = root.querySelector('[data-map-dialog]');
  const mapForm = root.querySelector('[data-map-form]');
  const validMapResponse = value => value && typeof value === 'object'
    && Number.isInteger(value.id) && value.id > 0 && typeof value.revision === 'string' && value.revision.length > 0
    && typeof value.name === 'string' && ['groups','nodes','edges'].every(key=>Array.isArray(value[key])
      && value[key].every(item=>item && typeof item === 'object' && Number.isInteger(item.id) && item.id>0));

  const post = (formData, {history = true} = {}) => {
    const execute = async () => {
      mutationBusy += 1;
      const before = lastSavedGraph ? structuredClone(lastSavedGraph) : null;
      try {
        if (graph?.revision && formData.get('action') !== 'create_map') {
          formData.set('base_revision', graph.revision);
        }
        const response = await fetch(root.dataset.apiUrl, {
          method: 'POST',
          headers: {'X-CSRFToken': csrf, 'Accept': 'application/json'},
          body: formData,
        });
        if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
          throw new Error('Unable to confirm the update. Check your session and refresh.');
        }
        let data;
        try { data = await response.json(); } catch (_error) { throw new Error('Invalid server response. The change was not confirmed.'); }
        if (!data || typeof data !== 'object' || (response.ok && !data.map && !(formData.get('action') === 'delete_map' && data.deleted === true))) {
          throw new Error('The server did not confirm the saved map. Refresh before retrying.');
        }
        if (data.map && (!validMapResponse(data.map) || (graph && formData.get('action') !== 'create_map' && data.map.id !== graph.id))) {
          throw new Error('Invalid saved map response. Refresh before retrying.');
        }
        if (data.map) {
          graph = data.map;
          lastSavedGraph = structuredClone(data.map);
        }
        if (!response.ok) {
          const error = new Error(data.error || `Unable to update System Map (${response.status}). Refresh and try again.`);
          error.isConflict = response.status === 409;
          if (error.isConflict) { undoStack.length = 0; redoStack.length = 0; }
          throw error;
        }
        if (history && before && data.map && formData.get('action') !== 'delete_map') {
          undoStack.push({before, after: structuredClone(data.map)});
          if (undoStack.length > 20) undoStack.shift();
          redoStack.length = 0;
        }
        return data;
      } catch (error) {
        if (graph && lastSavedGraph) {
          graph = structuredClone(lastSavedGraph);
          render();
        }
        throw error;
      } finally {
        mutationBusy = Math.max(0, mutationBusy - 1);
        root.querySelectorAll('[data-map-command="undo"]').forEach(button=>{button.disabled=mutationBusy>0||!undoStack.length;});
        root.querySelectorAll('[data-map-command="redo"]').forEach(button=>{button.disabled=mutationBusy>0||!redoStack.length;});
      }
    };
    const pending = mutationChain.then(execute, execute);
    mutationChain = pending.catch(() => {});
    return pending;
  };

  const openMapDialog = editing => {
    mapForm.reset();
    mapForm.querySelector('[data-dialog-error]').textContent = '';
    mapForm.elements.action.value = editing ? 'update_map' : 'create_map';
    mapForm.elements.map_id.value = editing && graph ? graph.id : '';
    mapForm.elements.name.value = editing && graph ? graph.name : '';
    mapForm.elements.description.value = editing && graph ? graph.description : '';
    root.querySelector('[data-map-dialog-title]').textContent = editing ? 'Edit Map' : 'New Map';
    const deleteMapButton = mapForm.querySelector('[data-delete-map]');
    if (deleteMapButton) deleteMapButton.hidden = !editing;
    mapDialog.showModal();
    mapForm.elements.name.focus();
  };

  mapForm.addEventListener('submit', async event => {
    event.preventDefault();
    const error = mapForm.querySelector('[data-dialog-error]');
    if (mutationBusy) {
      error.textContent = 'Wait for the current change to finish.';
      return;
    }
    const submit = mapForm.querySelector('[type="submit"]');
    error.textContent = '';
    submit.disabled = true;
    try {
      const result = await post(new FormData(mapForm));
      window.location.href = `${window.location.pathname}?map=${result.map?.id || ''}`;
    } catch (exception) {
      error.textContent = exception.message;
      if (exception.isConflict) mapDialog.close();
      submit.disabled = false;
    }
  });

  if (!graph) {
    root.addEventListener('click', event => {
      if (event.target.closest('[data-new-map]')) openMapDialog(false);
      else if (event.target.closest('[data-dialog-close]')) event.target.closest('dialog').close();
    });
    return;
  }

  const SURFACE_WIDTH = 1600;
  const SURFACE_HEIGHT = 1000;
  const NODE_WIDTH = 190;
  const NODE_HEIGHT = 76;
  const MIN_GROUP_WIDTH = 220;
  const MIN_GROUP_HEIGHT = 140;
  const COLLAPSED_GROUP_HEIGHT = 42;

  const viewport = root.querySelector('[data-map-viewport]');
  const surface = root.querySelector('[data-map-surface]');
  const nodeLayer = root.querySelector('[data-node-layer]');
  const groupLayer = root.querySelector('[data-group-layer]');
  const edgeLayer = root.querySelector('[data-edge-layer]');
  const workspace = root.querySelector('.relationship-workspace');
  const mapContextMenu = root.querySelector('[data-map-context-menu]');
  const edgeContextMenu = root.querySelector('[data-edge-context-menu]');
  const entityToolbar = root.querySelector('[data-entity-toolbar]');
  const entityContext = root.querySelector('[data-entity-context]');
  const edgeToolbar = root.querySelector('[data-edge-toolbar]');
  const lineStylePopover = root.querySelector('[data-line-style-popover]');
  const arrowPopover = root.querySelector('[data-arrow-popover]');
  const groupDrawPreview = root.querySelector('[data-group-draw-preview]');
  const minimap = root.querySelector('[data-minimap]');
  const statusElement = root.querySelector('[data-map-status]');
  const connectionHint = root.querySelector('[data-connection-hint]');

  let scale = 1;
  let panX = 20;
  let panY = 20;
  let hoveredNodeId = null;
  let selection = null;
  let activeConnection = null;
  let activeGroupDraw = null;
  let keyboardConnectionSourceId = null;
  let contextSurfacePoint = null;
  let drawingGroup = false;
  let suppressClickUntil = 0;
  let statusTimer = null;
  let selectedNodes = new Set();
  let selectedGroups = new Set();
  let suppressContextUntil = 0;
  let detailsDismissed = false;
  let searchIndex = -1;
  let pointerPosition = null;
  let selectingArea = false;
  let spaceHeld = false;
  let clipboard = null;
  let temporaryId = -1;
  let inlineName = null;
  let cancelActiveGesture = null;
  let lastEntityClick = null;
  root.addEventListener('pointerdown', event => {
    if (event.button !== 0 || !event.target.closest('[data-node-id],.relationship-group-header')) lastEntityClick=null;
  }, true);
  const PALETTE = [['#3B82F6', 'Blue'], ['#22C55E', 'Green'], ['#14B8A6', 'Teal'], ['#8B5CF6', 'Purple'], ['#F59E0B', 'Amber'], ['#F97316', 'Orange'], ['#EC4899', 'Pink'], ['#64748B', 'Gray']];

  const escapeHtml = value => String(value ?? '').replace(
    /[&<>'"]/g,
    char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[char]),
  );
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  let nodeIndexSource = null, nodeIndex = new Map(), siblingSource = null, siblingPairs = new Map();
  const nodeById = id => {
    if (nodeIndexSource !== graph.nodes) { nodeIndexSource=graph.nodes;nodeIndex=new Map(graph.nodes.map(node=>[node.id,node])); }
    return nodeIndex.get(Number(id));
  };
  const edgeById = id => graph.edges.find(edge => edge.id === Number(id));
  const groupById = id => graph.groups.find(group => group.id === Number(id));
  const groupNodes = groupId => graph.nodes.filter(node => node.group_id === Number(groupId));
  const selectedNodeIds = () => new Set([...selectedNodes, ...graph.nodes.filter(node => selectedGroups.has(node.group_id)).map(node => node.id)]);
  const queryText = () => root.querySelector('[data-node-search]').value.trim().toLocaleLowerCase();
  const nodeAvailable = node => !isNodeCollapsed(node) && (!queryText() || nodeMatchesQuery(node, queryText()));
  const existingConnection = (sourceId, targetId) => graph.edges.find(edge => (
    (edge.source_id === Number(sourceId) && edge.target_id === Number(targetId))
    || (edge.source_id === Number(targetId) && edge.target_id === Number(sourceId))
  ));
  const selectedItem = () => {
    if (!selection) return null;
    if (selection.kind === 'node') return nodeById(selection.id);
    if (selection.kind === 'edge') return edgeById(selection.id);
    return groupById(selection.id);
  };

  const showStatus = (message, isError = false) => {
    if (!statusElement) return;
    clearTimeout(statusTimer);
    statusElement.textContent = message;
    if (!isError && message === 'Delete selection') {
      statusElement.textContent = 'Deleted';
      const undoButton=document.createElement('button');undoButton.type='button';undoButton.dataset.mapCommand='undo';undoButton.className='map-status-undo';undoButton.textContent='Undo';statusElement.append(undoButton);
    }
    statusElement.classList.toggle('is-error', isError);
    statusElement.hidden = false;
    statusTimer = window.setTimeout(() => { statusElement.hidden = true; }, isError ? 6500 : message === 'Delete selection' ? 5000 : 2200);
  };

  const closeEdgePopovers = () => {
    [lineStylePopover, arrowPopover].forEach(element => {
      if (element) element.hidden = true;
    });
    edgeToolbar?.querySelector('[data-line-style-toggle]')?.setAttribute('aria-expanded', 'false');
    edgeToolbar?.querySelector('[data-arrow-toggle]')?.setAttribute('aria-expanded', 'false');
  };

  const hideFloatingControls = ({keepSelection = false} = {}) => {
    closeEdgePopovers();
    [mapContextMenu, edgeContextMenu, entityToolbar, entityContext, edgeToolbar].forEach(element => {
      if (element) element.hidden = true;
    });
    if (!keepSelection) { selection = null; selectedNodes.clear(); selectedGroups.clear(); }
  };

  const cancelGroupDrawing = () => {
    activeGroupDraw?.cancel?.();
    activeGroupDraw = null;
    drawingGroup = false;
    viewport.classList.remove('is-drawing-group');
    groupDrawPreview.hidden = true;
    connectionHint.hidden = true;
  };

  const positionFloating = (element, clientX, clientY) => {
    const bounds = workspace.getBoundingClientRect();
    element.hidden = false;
    const width = element.offsetWidth || 160;
    const height = element.offsetHeight || 44;
    element.style.left = `${clamp(clientX - bounds.left + 8, 8, bounds.width - width - 8)}px`;
    element.style.top = `${clamp(clientY - bounds.top + 8, 8, bounds.height - height - 8)}px`;
  };

  const clientToSurface = (clientX, clientY) => {
    const bounds = viewport.getBoundingClientRect();
    return {
      x: clamp((clientX - bounds.left - panX) / scale, -20000, 20000),
      y: clamp((clientY - bounds.top - panY) / scale, -20000, 20000),
    };
  };

  const postAction = values => {
    const formData = new FormData();
    Object.entries(values).forEach(([key, value]) => formData.append(key, value ?? ''));
    return post(formData);
  };

  const updateGraph = (result, message = 'Saved') => {
    if (result?.map) graph = result.map;
    if (selection && !selectedItem()) selection = null;
    render();
    showStatus(message);
  };

  const applyTransform = () => {
    hideFloatingControls({keepSelection: true});
    surface.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
    root.querySelector('[data-zoom-label]').textContent = `${Math.round(scale * 100)}%`;
    renderMinimap();
  };

  const nodeMatchesQuery = (node, query) => query.split(/\s+/).filter(Boolean).every(term => `${node.name} ${node.description || ''} ${groupById(node.group_id)?.name || ''}`.toLocaleLowerCase().includes(term));
  const isNodeCollapsed = node => {
    if (!node.group_id || !groupById(node.group_id)?.is_collapsed) return false;
    const query = root.querySelector('[data-node-search]').value.trim().toLocaleLowerCase();
    return !query || !nodeMatchesQuery(node, query);
  };

  const edgeGeometry = edge => {
    const source = nodeById(edge.source_id);
    const target = nodeById(edge.target_id);
    if (!source || !target || isNodeCollapsed(source) || isNodeCollapsed(target)) return null;

    const sourceCenter = {x: source.x + NODE_WIDTH / 2, y: source.y + NODE_HEIGHT / 2};
    const targetCenter = {x: target.x + NODE_WIDTH / 2, y: target.y + NODE_HEIGHT / 2};
    const dx = targetCenter.x - sourceCenter.x;
    const dy = targetCenter.y - sourceCenter.y;
    const distance = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / distance;
    const uy = dy / distance;
    const sourceDistance = Math.min(
      Math.abs(ux) < .001 ? Infinity : NODE_WIDTH / 2 / Math.abs(ux),
      Math.abs(uy) < .001 ? Infinity : NODE_HEIGHT / 2 / Math.abs(uy),
    );
    const targetDistance = sourceDistance;
    const x1 = sourceCenter.x + ux * sourceDistance;
    const y1 = sourceCenter.y + uy * sourceDistance;
    const x2 = targetCenter.x - ux * targetDistance;
    const y2 = targetCenter.y - uy * targetDistance;

    const pair = [edge.source_id, edge.target_id].sort((a, b) => a - b).join(':');
    if (siblingSource !== graph.edges) {
      siblingSource=graph.edges;siblingPairs=new Map();
      graph.edges.forEach(candidate=>{const key=[candidate.source_id,candidate.target_id].sort((a,b)=>a-b).join(':');if(!siblingPairs.has(key))siblingPairs.set(key,[]);siblingPairs.get(key).push(candidate);});
    }
    const siblings = siblingPairs.get(pair) || [];
    const siblingIndex = siblings.findIndex(candidate => candidate.id === edge.id);
    const siblingOffset = (siblingIndex - (siblings.length - 1) / 2) * 34;
    const normalX = -uy;
    const normalY = ux;
    const controlX = (x1 + x2) / 2 + normalX * siblingOffset;
    const controlY = (y1 + y2) / 2 + normalY * siblingOffset;
    return {
      x1, y1, x2, y2,
      path: `M ${x1} ${y1} Q ${controlX} ${controlY} ${x2} ${y2}`,
    };
  };

  const renderEdges = () => {
    edgeLayer.querySelectorAll('[data-rendered-edge]').forEach(item => item.remove());
    graph.edges.forEach(edge => {
      const geometry = edgeGeometry(edge);
      if (!geometry) return;
      const wrapper = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      wrapper.dataset.renderedEdge = '';
      wrapper.dataset.edgeId = edge.id;
      wrapper.setAttribute('tabindex', '0');
      wrapper.setAttribute('role', 'button');
      wrapper.setAttribute('aria-label', `${nodeById(edge.source_id)?.name || 'Node'} connected to ${nodeById(edge.target_id)?.name || 'Node'}`);
      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hit.setAttribute('d', geometry.path);
      hit.setAttribute('class', 'relationship-edge-hit');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', geometry.path);
      path.setAttribute('class', `relationship-edge${selection?.kind === 'edge' && selection.id === edge.id ? ' is-selected' : ''}`);
      path.setAttribute('stroke', edge.color || '#aeb7b1');
      if (edge.line_style === 'dashed') path.setAttribute('stroke-dasharray', '10 7');
      if (edge.direction !== 'none') path.setAttribute('marker-end', 'url(#map-arrow)');
      if (edge.direction === 'bidirectional') path.setAttribute('marker-start', 'url(#map-arrow)');
      wrapper.append(hit, path);
      edgeLayer.append(wrapper);
    });
  };

  const createSvg = (tag, attributes) => {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, String(value)));
    return element;
  };

  const renderMinimap = () => {
    if (!minimap) return;
    minimap.replaceChildren();
    const bounds = visibleBounds(true);
    if (bounds) minimap.setAttribute('viewBox', `${bounds.left - 40} ${bounds.top - 40} ${Math.max(300, bounds.right - bounds.left + 80)} ${Math.max(200, bounds.bottom - bounds.top + 80)}`);
    graph.groups.forEach(group => {
      minimap.append(createSvg('rect', {
        x: group.x,
        y: group.y,
        width: group.width,
        height: group.is_collapsed ? COLLAPSED_GROUP_HEIGHT : group.height,
        rx: 12,
        fill: group.color,
        opacity: .22,
        stroke: group.color,
        'stroke-width': 5,
      }));
    });
    graph.edges.forEach(edge => {
      const geometry = edgeGeometry(edge);
      if (!geometry) return;
      minimap.append(createSvg('path', {d: geometry.path, fill: 'none', stroke: '#87968e', 'stroke-width': 3}));
    });
    graph.nodes.filter(node => !isNodeCollapsed(node)).forEach(node => {
      minimap.append(createSvg('rect', {
        x: node.x, y: node.y, width: NODE_WIDTH, height: NODE_HEIGHT,
        rx: 8, fill: node.color || '#356b57', opacity: .8,
      }));
    });
    if (viewport?.clientWidth && viewport?.clientHeight) {
      minimap.append(createSvg('rect', {
        x: -panX / scale,
        y: -panY / scale,
        width: viewport.clientWidth / scale,
        height: viewport.clientHeight / scale,
        fill: 'none', stroke: '#ff1744', 'stroke-width': 7,
      }));
    }
  };

  const directRelations = nodeId => {
    const edges = graph.edges.filter(edge => edge.source_id === nodeId || edge.target_id === nodeId);
    return {
      edges: new Set(edges.map(edge => edge.id)),
      nodes: new Set([nodeId, ...edges.flatMap(edge => [edge.source_id, edge.target_id])]),
    };
  };

  const applyFocus = nodeId => {
    const ids = selectedNodeIds();
    const searching = Boolean(queryText());
    const activeId = searching ? null : (selection?.kind === 'node' && ids.size <= 1 ? selection.id : nodeId);
    surface.classList.toggle('has-focus', Boolean(activeId));
    const related = activeId ? directRelations(activeId) : {nodes: new Set(), edges: new Set()};
    nodeLayer.querySelectorAll('[data-node-id]').forEach(element => {
      const id = Number(element.dataset.nodeId);
      element.classList.toggle('is-focused', id === activeId || ids.has(id));
      element.classList.toggle('is-relative', related.nodes.has(id));
    });
    edgeLayer.querySelectorAll('[data-edge-id]').forEach(wrapper => {
      const active = related.edges.has(Number(wrapper.dataset.edgeId));
      wrapper.querySelector('.relationship-edge')?.classList.toggle('is-relative', active);
    });
  };

  const refreshGroupOptions = selectedValue => {
    const select = root.querySelector('[name="node_group_id"]');
    if (!select) return;
    const current = selectedValue === undefined ? select.value : String(selectedValue || '');
    select.innerHTML = '<option value="">No Group</option>' + graph.groups.map(group => (
      `<option value="${group.id}">${escapeHtml(group.name)}</option>`
    )).join('');
    select.value = graph.groups.some(group => String(group.id) === current) ? current : '';
  };

  const applyFilters = () => {
    const query = root.querySelector('[data-node-search]').value.trim().toLocaleLowerCase();
    const hasFilter = Boolean(query);
    surface.classList.toggle('has-search', hasFilter);
    groupLayer.querySelectorAll('[data-group-id]').forEach(element => {
      const group = groupById(element.dataset.groupId);
      const expandedBySearch = query && groupNodes(group.id).some(node => nodeMatchesQuery(node, query));
      element.classList.toggle('is-collapsed', group.is_collapsed && !expandedBySearch);
    });
    renderEdges();
    const visibleNodeIds = new Set();
    nodeLayer.querySelectorAll('[data-node-id]').forEach(element => {
      const node = nodeById(element.dataset.nodeId);
      element.hidden = isNodeCollapsed(node);
      const haystack = `${node.name} ${node.description || ''} ${groupById(node.group_id)?.name || ''}`.toLocaleLowerCase();
      const filtered = query && !query.split(/\s+/).every(term => haystack.includes(term));
      element.classList.toggle('is-filtered', Boolean(filtered));
      element.classList.toggle('is-search-match', Boolean(query && !filtered));
      if (!filtered && !element.hidden) visibleNodeIds.add(node.id);
    });
    edgeLayer.querySelectorAll('[data-edge-id]').forEach(element => {
      const edge = edgeById(element.dataset.edgeId);
      const filtered = hasFilter && (!visibleNodeIds.has(edge.source_id) || !visibleNodeIds.has(edge.target_id));
      element.querySelector('.relationship-edge')?.classList.toggle('is-filtered', filtered);
    });
    const count = root.querySelector('[data-search-count]');
    if (count) { count.hidden = !query; count.textContent = query ? `${visibleNodeIds.size} result${visibleNodeIds.size === 1 ? '' : 's'}` : ''; }
    root.querySelector('[data-map-command="fitResults"]').hidden = !query;
    applyFocus(hoveredNodeId);
  };

  const render = () => {
    if (inlineName && !inlineName.disabled) { inlineName = null; graph = structuredClone(lastSavedGraph); }
    selectedNodes = new Set([...selectedNodes].filter(id => nodeById(id)));
    selectedGroups = new Set([...selectedGroups].filter(id => groupById(id)));
    groupLayer.replaceChildren();
    graph.groups.forEach(group => {
      const nodeCount = groupNodes(group.id).length;
      const element = document.createElement('section');
      element.className = `relationship-group${group.is_collapsed ? ' is-collapsed' : ''}${selectedGroups.has(group.id) ? ' is-selected' : ''}`;
      element.tabIndex = 0;
      element.dataset.groupId = group.id;
      element.style.cssText = `left:${group.x}px;top:${group.y}px;width:${group.width}px;height:${group.height}px;--group-color:${group.color}`;
      element.innerHTML = `<div class="relationship-group-header">
        <span class="relationship-group-title">${escapeHtml(group.name)}</span>
        <span class="relationship-group-count">${nodeCount} Node${nodeCount === 1 ? '' : 's'}</span>
      </div>${!group.is_collapsed ? ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'].map(direction => `<button type="button" class="relationship-group-resize resize-${direction}" data-group-resize data-resize-direction="${direction}" aria-label="Resize Group with arrow keys (${direction})"></button>`).join('') : ''}`;
      groupLayer.append(element);
    });

    nodeLayer.replaceChildren();
    graph.nodes.forEach(node => {
      const element = document.createElement('article');
      element.className = `relationship-node${selectedNodes.has(node.id) || (selection?.kind === 'node' && selection.id === node.id) ? ' is-selected' : ''}`;
      element.tabIndex = 0;
      element.dataset.nodeId = node.id;
      element.hidden = isNodeCollapsed(node);
      element.style.cssText = `left:${node.x}px;top:${node.y}px;--node-color:${node.color}`;
      element.innerHTML = `<strong title="${escapeHtml(node.name)}">${escapeHtml(node.name)}</strong>${node.keep_position ? '<span class="node-pin" title="Position kept">⌖</span>' : ''}${node.external_url ? '<span class="node-link-mark" aria-hidden="true">↗</span>' : ''}<button type="button" class="node-connect-handle" data-connect-handle title="Drag to connect" aria-label="Connect from ${escapeHtml(node.name)}"></button>`;
      nodeLayer.append(element);
    });

    renderEdges();
    renderMinimap();
    root.querySelector('[data-map-empty]').hidden = graph.nodes.length !== 0;
    refreshGroupOptions();
    applyFilters();
    applyFocus(hoveredNodeId);
    renderNodeDetails();
    root.querySelector('[data-map-command="undo"]').disabled = mutationBusy > 0 || !undoStack.length;
    root.querySelector('[data-map-command="redo"]').disabled = mutationBusy > 0 || !redoStack.length;
  };

  const renderNodeDetails = () => {
    const node = selection?.kind === 'node' ? nodeById(selection.id) : null;
    const panel = root.querySelector('[data-node-details]');
    if (!panel) return;
    const visible = Boolean(node && !detailsDismissed);
    panel.hidden = !visible;
    workspace.classList.toggle('has-node-details', visible);
    panel.querySelector('[data-node-detail-name]').textContent = node?.name || 'Select a node';
    panel.querySelector('[data-node-detail-description]').textContent = node?.description || '';
    const link = panel.querySelector('[data-node-detail-link]');
    link.hidden = !node?.external_url;
    if (node?.external_url) link.href = node.external_url;
    else link.removeAttribute('href');
    panel.querySelector('[data-map-command="edit"]').disabled = !node;
  };

  const showEntityToolbar = (kind, id, clientX, clientY, focusToolbar = false, context = false) => {
    const item = kind === 'node' ? nodeById(id) : groupById(id);
    if (!item) return;
    cancelGroupDrawing();
    hideFloatingControls({keepSelection: true});
    selection = {kind, id: Number(id)};
    if (kind === 'node' && !selectedNodeIds().has(Number(id))) { selectedNodes = new Set([Number(id)]); selectedGroups.clear(); }
    if (kind === 'group' && !selectedGroups.has(Number(id))) { selectedGroups = new Set([Number(id)]); selectedNodes.clear(); }
    detailsDismissed = false;
    if (kind === 'node') {
      nodeLayer.querySelectorAll('[data-node-id]').forEach(element => element.classList.toggle('is-selected', selectedNodes.has(Number(element.dataset.nodeId))));
      groupLayer.querySelectorAll('[data-group-id]').forEach(element => element.classList.toggle('is-selected', selectedGroups.has(Number(element.dataset.groupId))));
      applyFocus(hoveredNodeId); renderNodeDetails();
    } else render();
    entityToolbar.innerHTML = `<button type="button" data-map-command="edit">Edit</button><button type="button" class="text-danger" data-map-command="remove">Delete</button>`;
    const toolbar = context ? entityContext : entityToolbar;
    entityContext.querySelector('[data-context-collapse]').hidden = !selectedGroups.size;
    entityContext.querySelector('[data-map-command="pin"]').hidden = !selectedNodeIds().size;
    entityContext.querySelector('[data-arrange-select]').hidden = selectedNodeIds().size < 2;
    positionFloating(toolbar, clientX, clientY);
    if (focusToolbar) toolbar.querySelector('button')?.focus();
  };

  const syncEdgeToolbar = edge => {
    const preview = edgeToolbar.querySelector('[data-line-style-preview]');
    preview.classList.toggle('dashed', edge.line_style === 'dashed');
    preview.classList.toggle('solid', edge.line_style !== 'dashed');
    lineStylePopover.querySelectorAll('[data-line-style]').forEach(button => {
      button.setAttribute('aria-checked', String(button.dataset.lineStyle === (edge.line_style || 'solid')));
    });
    const arrowToggle = edgeToolbar.querySelector('[data-arrow-toggle]');
    const arrowStates = {
      none: 'No arrow',
      forward: 'One-way arrow',
      bidirectional: 'Two-way arrow',
    };
    const arrowState = arrowStates[edge.direction] || arrowStates.forward;
    arrowToggle.textContent = '>';
    arrowToggle.title = `Arrow direction: ${arrowState}`;
    arrowToggle.setAttribute('aria-label', `Arrow direction: ${arrowState}`);
    arrowPopover.querySelectorAll('[data-arrow-direction]').forEach(button => {
      button.setAttribute('aria-checked', String(button.dataset.arrowDirection === edge.direction));
    });
  };

  const showEdgeToolbar = (edgeId, clientX, clientY, focusToolbar = false) => {
    const edge = edgeById(edgeId);
    if (!edge) return;
    cancelGroupDrawing();
    hideFloatingControls({keepSelection: true});
    selection = {kind: 'edge', id: Number(edgeId)};
    render();
    syncEdgeToolbar(edge);
    positionFloating(edgeToolbar, clientX, clientY);
    if (focusToolbar) edgeToolbar.querySelector('button')?.focus();
  };

  const updateSelectedEdge = async changes => {
    const edge = selection?.kind === 'edge' ? edgeById(selection.id) : null;
    if (!edge || mutationBusy) return;
    try {
      const result = await postAction({
        action: 'update_edge', map_id: graph.id, edge_id: edge.id,
        source_id: edge.source_id, target_id: edge.target_id,
        direction: changes.direction ?? edge.direction,
        line_style: changes.line_style ?? edge.line_style ?? 'solid',
      });
      updateGraph(result, 'Connection updated');
      const updated = edgeById(edge.id);
      if (updated) syncEdgeToolbar(updated);
    } catch (error) { showStatus(error.message, true); }
  };

  const entityDialog = root.querySelector('[data-entity-dialog]');
  const entityForm = root.querySelector('[data-entity-form]');

  const spawnPosition = kind => {
    const width = kind === 'group' ? 360 : NODE_WIDTH;
    const height = kind === 'group' ? 260 : NODE_HEIGHT;
    const count = kind === 'group' ? graph.groups.length : graph.nodes.length;
    const offset = (count % 7) * 24;
    const centerX = (viewport.clientWidth / 2 - panX) / scale;
    const centerY = (viewport.clientHeight / 2 - panY) / scale;
    return {
      x: clamp(centerX - width / 2 + offset, -20000, 20000 - width),
      y: clamp(centerY - height / 2 + offset, -20000, 20000 - height),
    };
  };

  const openEntityDialog = (kind, item = null, preset = {}) => {
    entityForm.reset();
    entityForm.querySelector('[data-dialog-error]').textContent = '';
    entityForm.querySelectorAll('[data-node-fields],[data-group-fields]').forEach(section => { section.hidden = true; });
    entityForm.elements.map_id.value = graph.id;
    const position = item ? item : (Object.keys(preset).length ? preset : spawnPosition(kind));
    entityForm.elements.x.value = item?.x ?? position.x ?? 180;
    entityForm.elements.y.value = item?.y ?? position.y ?? 150;

    if (kind === 'node') {
      entityForm.elements.action.value = item ? 'update_node' : 'create_node';
      entityForm.elements.node_id.value = item?.id || '';
      const fields = entityForm.querySelector('[data-node-fields]');
      fields.hidden = false;
      fields.querySelector('[name="name"]').value = item?.name || '';
      ['description', 'external_url'].forEach(name => {
        fields.querySelector(`[name="${name}"]`).value = item?.[name] || '';
      });
      refreshGroupOptions(item?.group_id || (!item ? groupAtPoint(position.x + NODE_WIDTH / 2, position.y + NODE_HEIGHT / 2)?.id : '') || '');
      fields.querySelector('[name="color"]').value = item?.color || '#3B82F6';
    } else if (kind === 'group') {
      entityForm.elements.action.value = item ? 'update_group' : 'create_group';
      entityForm.elements.group_id.value = item?.id || '';
      const fields = entityForm.querySelector('[data-group-fields]');
      fields.hidden = false;
      fields.querySelector('[name="group_name"]').value = item?.name || '';
      fields.querySelector('[name="group_color"]').value = item?.color || '#14B8A6';
      fields.querySelector('[name="group_width"]').value = Math.round(item?.width ?? position.width ?? 360);
      fields.querySelector('[name="group_height"]').value = Math.round(item?.height ?? position.height ?? 260);
      fields.querySelector('[name="group_collapsed"]').value = String(Boolean(item?.is_collapsed));
    }

    root.querySelector('[data-entity-dialog-title]').textContent = `${item ? 'Edit' : 'New'} ${kind[0].toUpperCase()}${kind.slice(1)}`;
    renderPalette();
    entityDialog.showModal();
    entityForm.querySelector(`[data-${kind}-fields] input:not([type="hidden"]), [data-${kind}-fields] select`)?.focus();
  };

  const entityForKind = (kind, id) => {
    if (kind === 'node') return nodeById(id);
    if (kind === 'edge') return edgeById(id);
    return groupById(id);
  };

  const deleteEntity = async (kind, id) => {
    if (mutationBusy) return showStatus('Wait for the current change to finish.', true);
    const item = entityForKind(kind, id);
    const extra = kind === 'group' ? ` Its ${groupNodes(id).length} Nodes will become ungrouped.` : '';
    if (!item || (kind === 'group' && !window.confirm(`Delete this ${kind}?${extra}`))) return;
    const result = await postAction({action: `delete_${kind}`, map_id: graph.id, [`${kind}_id`]: id});
    selection = null;
    updateGraph(result, `${kind[0].toUpperCase()}${kind.slice(1)} deleted`);
  };

  const cancelKeyboardConnection = ({rerender = false} = {}) => {
    keyboardConnectionSourceId = null;
    connectionHint.hidden = true;
    if (rerender) render();
  };

  const armKeyboardConnection = sourceId => {
    const source = nodeById(sourceId);
    if (!source) return;
    hideFloatingControls({keepSelection: true});
    keyboardConnectionSourceId = source.id;
    selection = {kind: 'node', id: source.id};
    render();
    const sourceElement = nodeLayer.querySelector(`[data-node-id="${source.id}"]`);
    sourceElement?.classList.add('is-connecting');
    sourceElement?.focus();
    connectionHint.textContent = 'Tab to another Node and press Enter · Esc to cancel';
    connectionHint.hidden = false;
  };

  const quickConnect = async (sourceId, targetId, clientX, clientY, focusToolbar = false) => {
    if (Number(sourceId) === Number(targetId)) return showStatus('A Node cannot connect to itself.', true);
    cancelKeyboardConnection();
    const duplicate = existingConnection(sourceId, targetId);
    if (duplicate) {
      selection = {kind: 'edge', id: duplicate.id};
      render();
      showEdgeToolbar(duplicate.id, clientX, clientY, focusToolbar);
      showStatus('These Nodes are already connected. The existing connection is selected.', true);
      return;
    }
    if (mutationBusy) return showStatus('Wait for the current change to finish.', true);
    try {
      const result = await postAction({
        action: 'create_edge', map_id: graph.id, source_id: sourceId, target_id: targetId,
        direction: 'none', line_style: 'solid',
      });
      graph = result.map;
      const created = existingConnection(sourceId, targetId);
      selection = created ? {kind: 'edge', id: created.id} : null;
      updateGraph(result, 'Connection created');
      if (created) showEdgeToolbar(created.id, clientX, clientY, focusToolbar);
    } catch (error) { showStatus(error.message, true); }
  };

  root.addEventListener('click', async event => {
    if (event.target.closest('[data-new-map]')) return openMapDialog(false);
    if (event.target.closest('[data-edit-map]')) return openMapDialog(true);
    if (event.target.closest('[data-dialog-close]')) return event.target.closest('dialog').close();

    if (event.target.closest('[data-delete-map]')) {
      if (!window.confirm(`Move Map “${graph.name}” to Trash? It can be restored for 30 days.`)) return;
      try {
        await postAction({action: 'delete_map', map_id: graph.id});
        window.location.href = window.location.pathname;
      } catch (error) { mapForm.querySelector('[data-dialog-error]').textContent = error.message; }
      return;
    }

    const createButton = event.target.closest('[data-context-create]');
    if (createButton) {
      const kind = createButton.dataset.contextCreate;
      mapContextMenu.hidden = true;
      cancelGroupDrawing();
      if (kind === 'node') {
        const point = contextSurfacePoint || spawnPosition('node');
        pointerPosition = {x: point.x, y: point.y};
        return quickNode();
      }
      drawingGroup = true;
      viewport.classList.add('is-drawing-group');
      connectionHint.textContent = 'Drag on the canvas to draw a Group · Esc to cancel';
      connectionHint.hidden = false;
      return;
    }

    const floatingEdit = event.target.closest('[data-floating-edit]');
    if (floatingEdit) {
      const kind = floatingEdit.dataset.floatingEdit;
      hideFloatingControls({keepSelection: true});
      return openEntityDialog(kind, entityForKind(kind, floatingEdit.dataset.id));
    }
    const floatingDelete = event.target.closest('[data-floating-delete]');
    if (floatingDelete) {
      try { await deleteEntity(floatingDelete.dataset.floatingDelete, floatingDelete.dataset.id); }
      catch (error) { showStatus(error.message, true); }
      return;
    }
    if (event.target.closest('[data-delete-selected-edge]')) {
      edgeContextMenu.hidden = true;
      if (selection?.kind === 'edge') {
        try { await deleteEntity('edge', selection.id); }
        catch (error) { showStatus(error.message, true); }
      }
      return;
    }
    const lineStyleToggle = event.target.closest('[data-line-style-toggle]');
    if (lineStyleToggle) {
      const shouldOpen = lineStylePopover.hidden;
      closeEdgePopovers();
      if (shouldOpen) {
        lineStylePopover.hidden = false;
        lineStyleToggle.setAttribute('aria-expanded', 'true');
        lineStylePopover.querySelector('[aria-checked="true"]')?.focus();
      }
      return;
    }
    const lineStyle = event.target.closest('[data-line-style]');
    if (lineStyle) {
      const returnFocus = edgeToolbar.querySelector('[data-line-style-toggle]');
      closeEdgePopovers();
      await updateSelectedEdge({line_style: lineStyle.dataset.lineStyle});
      returnFocus?.focus();
      return;
    }
    const arrowToggle = event.target.closest('[data-arrow-toggle]');
    if (arrowToggle) {
      const shouldOpen = arrowPopover.hidden;
      closeEdgePopovers();
      if (shouldOpen) {
        arrowPopover.hidden = false;
        arrowToggle.setAttribute('aria-expanded', 'true');
        arrowPopover.querySelector('[aria-checked="true"]')?.focus();
      }
      return;
    }
    const arrowDirection = event.target.closest('[data-arrow-direction]');
    if (arrowDirection) {
      const returnFocus = edgeToolbar.querySelector('[data-arrow-toggle]');
      closeEdgePopovers();
      await updateSelectedEdge({direction: arrowDirection.dataset.arrowDirection});
      returnFocus?.focus();
      return;
    }

    if (performance.now() < suppressClickUntil) return;
    const connectButton = event.target.closest('[data-connect-handle]');
    if (connectButton) {
      const sourceElement = connectButton.closest('[data-node-id]');
      if (sourceElement) armKeyboardConnection(sourceElement.dataset.nodeId);
      return;
    }
    const nodeElement = event.target.closest('[data-node-id]');
    if (nodeElement) {
      if (keyboardConnectionSourceId) {
        const sourceId = keyboardConnectionSourceId;
        if (Number(nodeElement.dataset.nodeId) !== sourceId) {
          await quickConnect(sourceId, nodeElement.dataset.nodeId, event.clientX, event.clientY);
        }
        return;
      }
      showEntityToolbar('node', nodeElement.dataset.nodeId, event.clientX, event.clientY);
      return;
    }

    const edgeElement = event.target.closest('[data-edge-id]');
    if (edgeElement) return showEdgeToolbar(edgeElement.dataset.edgeId, event.clientX, event.clientY);
    const groupElement = event.target.closest('[data-group-id]');
    if (groupElement) return showEntityToolbar('group', groupElement.dataset.groupId, event.clientX, event.clientY);
    if (event.target === viewport || event.target === surface) {
      hideFloatingControls();
      render();
    }
  });

  document.addEventListener('click', event => {
    if (!edgeToolbar.contains(event.target)) closeEdgePopovers();
  });

  edgeToolbar.addEventListener('keydown', event => {
    const popover = event.target.closest('.edge-tool-popover');
    if (!popover) return;
    const choices = [...popover.querySelectorAll('button')];
    const currentIndex = choices.indexOf(event.target);
    let nextIndex = null;
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % choices.length;
    if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + choices.length) % choices.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = choices.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    choices[nextIndex].focus();
  });

  const showCanvasContext = event => {
    cancelGroupDrawing();
    cancelKeyboardConnection();
    const edgeElement = event.target.closest('[data-edge-id]');
    if (edgeElement) {
      hideFloatingControls({keepSelection: true});
      selection = {kind: 'edge', id: Number(edgeElement.dataset.edgeId)};
      render();
      positionFloating(edgeContextMenu, event.clientX, event.clientY);
      return;
    }
    const nodeElement = event.target.closest('[data-node-id]');
    if (nodeElement) return showEntityToolbar('node', nodeElement.dataset.nodeId, event.clientX, event.clientY, false, true);
    const groupElement = event.target.closest('[data-group-id]');
    if (groupElement) return showEntityToolbar('group', groupElement.dataset.groupId, event.clientX, event.clientY, false, true);
    hideFloatingControls();
    contextSurfacePoint = clientToSurface(event.clientX, event.clientY);
    mapContextMenu.querySelector('[data-context-paste]').hidden = !clipboard;
    positionFloating(mapContextMenu, event.clientX, event.clientY);
  };
  viewport.addEventListener('contextmenu', event => {
    if (event.target.closest('input,textarea,[contenteditable="true"]')) return;
    event.preventDefault();
    if (cancelActiveGesture || performance.now() < suppressContextUntil) return;
    showCanvasContext(event);
  });

  nodeLayer.addEventListener('pointerover', event => {
    const node = event.target.closest('[data-node-id]');
    if (node) { hoveredNodeId = Number(node.dataset.nodeId); applyFocus(hoveredNodeId); }
  });
  nodeLayer.addEventListener('pointerout', event => {
    const node = event.target.closest('[data-node-id]');
    if (node && !node.contains(event.relatedTarget)) { hoveredNodeId = null; applyFocus(null); }
  });

  entityForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (mutationBusy) return showStatus('Wait for the current change to finish.', true);
    const error = entityForm.querySelector('[data-dialog-error]');
    const submit = entityForm.querySelector('[type="submit"]');
    error.textContent = '';
    submit.disabled = true;
    const formData = new FormData(entityForm);
    const action = String(formData.get('action'));
    if (action.includes('group')) {
      const fields = entityForm.querySelector('[data-group-fields]');
      formData.set('name', fields.querySelector('[name="group_name"]').value);
      formData.set('color', fields.querySelector('[name="group_color"]').value);
      formData.set('width', fields.querySelector('[name="group_width"]').value);
      formData.set('height', fields.querySelector('[name="group_height"]').value);
      formData.set('is_collapsed', fields.querySelector('[name="group_collapsed"]').value || 'false');
    }
    if (action.includes('node')) {
      const fields = entityForm.querySelector('[data-node-fields]');
      ['name', 'description', 'color', 'external_url'].forEach(name => {
        formData.set(name, fields.querySelector(`[name="${name}"]`).value);
      });
      formData.set('group_id', fields.querySelector('[name="node_group_id"]').value);
    }
    try {
      const document = structuredClone(graph);
      if (action.includes('node')) {
        const id = Number(formData.get('node_id')) || temporaryId--;
        const old = document.nodes.find(item => item.id === id);
        const groupId = Number(formData.get('group_id')) || null;
        const node = {...old, id, name: String(formData.get('name') || '').trim(), description: String(formData.get('description') || ''), color: String(formData.get('color') || '#3B82F6'), external_url: String(formData.get('external_url') || '').trim(), group_id: groupId, x: Number(formData.get('x')), y: Number(formData.get('y')), keep_position: old?.keep_position || false};
        if (groupId && (!old || old.group_id !== groupId)) {
          const group = document.groups.find(item => item.id === groupId);
          const columns = Math.max(1, Math.floor((group.width - 20) / (NODE_WIDTH + 20)));
          let index = 0;
          do { node.x = group.x + 20 + index % columns * (NODE_WIDTH + 20); node.y = group.y + 52 + Math.floor(index / columns) * (NODE_HEIGHT + 24); index++; }
          while (document.nodes.some(other => other.id !== id && other.group_id === groupId && Math.abs(other.x - node.x) < NODE_WIDTH + 8 && Math.abs(other.y - node.y) < NODE_HEIGHT + 8));
        }
        if (old) Object.assign(old, node); else document.nodes.push(node);
      } else {
        const id = Number(formData.get('group_id')) || temporaryId--;
        const old = document.groups.find(item => item.id === id);
        const group = {...old, id, name: String(formData.get('name') || '').trim(), color: String(formData.get('color') || '#14B8A6'), x: Number(formData.get('x')), y: Number(formData.get('y')), width: Number(formData.get('width')), height: Number(formData.get('height')), is_collapsed: formData.get('is_collapsed') === 'true'};
        if (old) Object.assign(old, group); else document.groups.push(group);
      }
      await commitDocument(document, action.startsWith('create') ? 'Add entity' : 'Edit entity');
      entityDialog.close();
    } catch (exception) {
      error.textContent = exception.message;
      if (exception.isConflict) {
        entityDialog.close();
        showStatus(exception.message, true);
      }
    } finally {
      submit.disabled = false;
    }
  });

  root.querySelector('[data-map-select]')?.addEventListener('change', event => {
    if (event.target.value) window.location.href = `${window.location.pathname}?map=${event.target.value}`;
  });
  root.querySelector('[data-node-search]').addEventListener('input', () => { searchIndex = -1; hoveredNodeId = null; applyFilters(); });
  root.querySelector('[data-zoom-in]').addEventListener('click', () => runCommand('zoomIn'));
  root.querySelector('[data-zoom-out]').addEventListener('click', () => runCommand('zoomOut'));

  const visibleBounds = (includeCollapsedContent = false) => {
    const boxes = [];
    graph.groups.forEach(group => boxes.push({
      left: group.x, top: group.y, right: group.x + group.width,
      bottom: group.y + (group.is_collapsed && !includeCollapsedContent ? COLLAPSED_GROUP_HEIGHT : group.height),
    }));
    graph.nodes.filter(node => includeCollapsedContent || !isNodeCollapsed(node)).forEach(node => boxes.push({
      left: node.x, top: node.y, right: node.x + NODE_WIDTH, bottom: node.y + NODE_HEIGHT,
    }));
    if (!boxes.length) return null;
    return {
      left: Math.min(...boxes.map(box => box.left)),
      top: Math.min(...boxes.map(box => box.top)),
      right: Math.max(...boxes.map(box => box.right)),
      bottom: Math.max(...boxes.map(box => box.bottom)),
    };
  };

  root.querySelector('[data-fit-map]').addEventListener('click', () => {
    if (cancelActiveGesture || activeConnection || activeGroupDraw || inlineName) return;
    const bounds = visibleBounds();
    if (!bounds) return;
    const width = Math.max(1, bounds.right - bounds.left);
    const height = Math.max(1, bounds.bottom - bounds.top);
    scale = Math.max(.02, Math.min(1.25, (viewport.clientWidth - 80) / width, (viewport.clientHeight - 80) / height));
    panX = (viewport.clientWidth - width * scale) / 2 - bounds.left * scale;
    panY = (viewport.clientHeight - height * scale) / 2 - bounds.top * scale;
    applyTransform();
  });
  root.querySelector('[data-overview-fit]').addEventListener('click', () => root.querySelector('[data-fit-map]').click());

  const localGroupLayout = group => {
    const members = groupNodes(group.id);
    if (members.some(node => node.keep_position)) return;
    const columns = Math.max(1, Math.min(6, Math.ceil(Math.sqrt(Math.max(1, members.length)))));
    const rows = Math.max(1, Math.ceil(members.length / columns));
    group.width = Math.max(MIN_GROUP_WIDTH, columns * 210 + 30);
    group.height = Math.max(MIN_GROUP_HEIGHT, rows * 100 + 60);
    members.forEach((node, index) => {
      node.x = group.x + 20 + (index % columns) * 210;
      node.y = group.y + 52 + Math.floor(index / columns) * 100;
    });
  };

  const applyAutomaticLayout = layout => {
    if (layout === 'grid') {
      let x = 60;
      let y = 60;
      let rowHeight = 0;
      graph.groups.forEach(group => {
        if (groupNodes(group.id).some(node => node.keep_position)) return;
        localGroupLayout(group);
        if (x + group.width > SURFACE_WIDTH - 40) { x = 60; y += rowHeight + 45; rowHeight = 0; }
        group.x = x;
        group.y = y;
        localGroupLayout(group);
        x += group.width + 45;
        rowHeight = Math.max(rowHeight, group.height);
      });
      const ungrouped = graph.nodes.filter(node => !node.group_id && !node.keep_position);
      const startY = graph.groups.length ? Math.min(SURFACE_HEIGHT - 120, y + rowHeight + 60) : 80;
      ungrouped.forEach((node, index) => {
        node.x = 80 + (index % 6) * 240;
        node.y = startY + Math.floor(index / 6) * 120;
      });
    } else {
      const centerX = SURFACE_WIDTH / 2;
      const centerY = SURFACE_HEIGHT / 2;
      const radius = Math.min(360, Math.max(210, graph.groups.length * 65));
      graph.groups.forEach((group, index) => {
        if (groupNodes(group.id).some(node => node.keep_position)) return;
        localGroupLayout(group);
        const angle = graph.groups.length ? index / graph.groups.length * Math.PI * 2 : 0;
        group.x = clamp(centerX + Math.cos(angle) * radius - group.width / 2, 20, SURFACE_WIDTH - group.width - 20);
        group.y = clamp(centerY + Math.sin(angle) * radius - group.height / 2, 20, SURFACE_HEIGHT - group.height - 20);
        localGroupLayout(group);
      });
      const ungrouped = graph.nodes.filter(node => !node.group_id && !node.keep_position);
      ungrouped.forEach((node, index) => {
        const angle = ungrouped.length ? index / ungrouped.length * Math.PI * 2 : 0;
        node.x = clamp(centerX + Math.cos(angle) * 430 - NODE_WIDTH / 2, 10, SURFACE_WIDTH - NODE_WIDTH - 10);
        node.y = clamp(centerY + Math.sin(angle) * 430 - NODE_HEIGHT / 2, 10, SURFACE_HEIGHT - NODE_HEIGHT - 10);
      });
    }
  };

  root.querySelector('[data-layout-select]').addEventListener('change', async event => {
    const layout = event.target.value;
    event.target.value = '';
    if (!layout || (!graph.nodes.length && !graph.groups.length)) return;
    if (mutationBusy || cancelActiveGesture || activeConnection || activeGroupDraw || inlineName) return showStatus('Finish the current edit first.', true);
    applyAutomaticLayout(layout);
    render();
    try {
      await commitDocument(structuredClone(graph), 'Auto layout');
    } catch (error) { showStatus(error.message, true); }
  });

  const groupAtPoint = (x, y) => [...graph.groups].reverse().find(group => (
    !group.is_collapsed && x >= group.x && x <= group.x + group.width && y >= group.y && y <= group.y + group.height
  ));

  const saveLayout = async ({nodes = [], groups = []}, message = 'Position saved') => {
    const result = await postAction({
      action: 'layout_entities', map_id: graph.id,
      nodes: JSON.stringify(nodes), groups: JSON.stringify(groups),
    });
    updateGraph(result, message);
  };

  const renderPalette = () => {
    entityForm.querySelectorAll('[data-color-palette]').forEach(palette => {
      const field = entityForm.elements[palette.dataset.colorPalette];
      palette.replaceChildren(...PALETTE.map(([color, label]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.style.backgroundColor = color;
        button.title = label;
        button.setAttribute('aria-label', `${label} color`);
        button.setAttribute('aria-pressed', String(field.value.toUpperCase() === color));
        button.textContent = field.value.toUpperCase() === color ? '✓' : '';
        button.addEventListener('click', () => { field.value = color; renderPalette(); });
        return button;
      }));
      const legacy = root.querySelector(`[data-current-color="${palette.dataset.colorPalette}"]`);
      legacy.textContent = '';
    });
  };

  const growGroups = document => {
    document.groups.forEach(group => {
      const members = document.nodes.filter(node => node.group_id === group.id);
      if (!members.length) return;
      const minX = Math.min(group.x, ...members.map(node => node.x - 20));
      const minY = Math.min(group.y, ...members.map(node => node.y - 52));
      const right = Math.max(group.x + group.width, ...members.map(node => node.x + NODE_WIDTH + 20));
      const bottom = Math.max(group.y + group.height, ...members.map(node => node.y + NODE_HEIGHT + 20));
      Object.assign(group, {x: minX, y: minY, width: Math.max(220, right - minX), height: Math.max(140, bottom - minY)});
    });
  };

  const commitDocument = async (document, label, {history = true} = {}) => {
    if (mutationBusy) throw new Error('Wait for the current change to finish.');
    growGroups(document);
    const payload = new FormData();
    payload.set('action', 'save_document');
    payload.set('map_id', graph.id);
    payload.set('label', label);
    payload.set('document', JSON.stringify(document));
    const result = await post(payload, {history});
    updateGraph(result, label);
    return result;
  };

  const historyStep = async redo => {
    if (mutationBusy) return;
    const from = redo ? redoStack : undoStack;
    const to = redo ? undoStack : redoStack;
    const entry = from.at(-1);
    if (!entry) return;
    try {
      await commitDocument(structuredClone(redo ? entry.after : entry.before), redo ? 'Redo edit' : 'Undo edit', {history: false});
      from.pop(); to.push(entry);
      selectedNodes.clear(); selectedGroups.clear(); selection = null; render();
    } catch (error) { showStatus(error.message, true); }
  };

  const matchedNodes = () => {
    const terms = root.querySelector('[data-node-search]').value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return graph.nodes.filter(node => !isNodeCollapsed(node) && terms.every(term => `${node.name} ${node.description || ''} ${groupById(node.group_id)?.name || ''}`.toLocaleLowerCase().includes(term)));
  };
  const fitNodes = nodes => {
    if (!nodes.length) return;
    const x = Math.min(...nodes.map(node => node.x));
    const y = Math.min(...nodes.map(node => node.y));
    const width = Math.max(...nodes.map(node => node.x + NODE_WIDTH)) - x;
    const height = Math.max(...nodes.map(node => node.y + NODE_HEIGHT)) - y;
    scale = clamp(Math.min((viewport.clientWidth - 70) / width, (viewport.clientHeight - 70) / height), .02, 1.3);
    panX = (viewport.clientWidth - width * scale) / 2 - x * scale;
    panY = (viewport.clientHeight - height * scale) / 2 - y * scale;
    applyTransform();
  };
  const visitSearch = previous => {
    const matches = matchedNodes();
    if (!matches.length) return;
    searchIndex = searchIndex < 0 ? (previous ? matches.length - 1 : 0) : (searchIndex + (previous ? -1 : 1) + matches.length) % matches.length;
    const node = matches[searchIndex];
    selection = {kind: 'node', id: node.id}; selectedNodes = new Set([node.id]); selectedGroups.clear(); detailsDismissed = false;
    panX = viewport.clientWidth / 2 - (node.x + NODE_WIDTH / 2) * scale;
    panY = viewport.clientHeight / 2 - (node.y + NODE_HEIGHT / 2) * scale;
    render(); applyTransform();
  };

  const copySelection = () => {
    const ids = selectedNodeIds();
    if (!ids.size && !selectedGroups.size) return;
    clipboard = {
      revision: graph.revision,
      groups: structuredClone(graph.groups.filter(group => selectedGroups.has(group.id))),
      nodes: structuredClone(graph.nodes.filter(node => ids.has(node.id))),
      edges: structuredClone(graph.edges.filter(edge => ids.has(edge.source_id) && ids.has(edge.target_id))),
    };
    showStatus('Selection copied');
  };
  const pasteSelection = async (duplicate, point = null) => {
    if (duplicate) copySelection();
    if (!clipboard || mutationBusy || inlineName || cancelActiveGesture || activeConnection || activeGroupDraw) return;
    const document = structuredClone(graph);
    const groupIds = new Map();
    const nodeIds = new Map();
    const names = new Set(document.groups.map(group => group.name.toLowerCase()));
    const copiedBoxes = [...clipboard.nodes, ...clipboard.groups];
    const offsetX = point ? point.x - Math.min(...copiedBoxes.map(item => item.x)) : 40;
    let offsetY = point ? point.y - Math.min(...copiedBoxes.map(item => item.y)) : 40;
    while (clipboard.nodes.some(node => document.nodes.some(other => Math.abs(other.x - (node.x + offsetX)) < NODE_WIDTH + 12 && Math.abs(other.y - (node.y + offsetY)) < NODE_HEIGHT + 12)) && offsetY < 20000) offsetY += NODE_HEIGHT + 24;
    for (const original of clipboard.groups) {
      const group = structuredClone(original);
      group.id = temporaryId--; group._copied_from = original.id; group._copied_revision = clipboard.revision; group.x += offsetX; group.y += offsetY;
      const baseName = group.name.slice(0, 100);
      let name = `${baseName} copy`, index = 2;
      while (names.has(name.toLowerCase())) name = `${baseName} copy ${index++}`;
      group.name = name; names.add(group.name.toLowerCase());
      groupIds.set(original.id, group.id); document.groups.push(group);
    }
    for (const original of clipboard.nodes) {
      const node = {...original, id: temporaryId--, _copied_from: original.id, _copied_revision: clipboard.revision, x: original.x + offsetX, y: original.y + offsetY, keep_position: false};
      node.group_id = groupIds.get(original.group_id) ?? groupAtPoint(node.x + NODE_WIDTH / 2, node.y + NODE_HEIGHT / 2)?.id ?? null;
      nodeIds.set(original.id, node.id); document.nodes.push(node);
    }
    for (const edge of clipboard.edges) document.edges.push({...edge, id: temporaryId--, _copied_from: edge.id, _copied_revision: clipboard.revision, source_id: nodeIds.get(edge.source_id), target_id: nodeIds.get(edge.target_id)});
    const oldIds = new Set(graph.nodes.map(node => node.id));
    const oldGroups = new Set(graph.groups.map(group => group.id));
    try {
      await commitDocument(document, duplicate ? 'Duplicate selection' : 'Paste selection');
      selectedNodes = new Set(graph.nodes.filter(node => !oldIds.has(node.id)).map(node => node.id));
      selectedGroups = new Set(graph.groups.filter(group => !oldGroups.has(group.id)).map(group => group.id));
      selection = selectedNodes.size ? {kind: 'node', id: [...selectedNodes][0]} : null;
      if (!selection && selectedGroups.size) selection = {kind: 'group', id: [...selectedGroups][0]};
      render(); viewport.focus({preventScroll:true});
    } catch (error) { showStatus(error.message, true); }
  };

  const renameInline = (node, draft, label = 'Rename node') => {
    if (!node || inlineName || mutationBusy) return;
    if (draft) { graph = draft; render(); }
    const element = nodeLayer.querySelector(`[data-node-id="${node.id}"]`);
    if (!element) return;
    const input = document.createElement('input');
    input.className = 'map-inline-name'; input.value = node.name; input.maxLength = 160;
    input.setAttribute('aria-label', 'Node name');
    inlineName = input; element.append(input); input.focus(); input.select();
    input.addEventListener('blur', () => {
      // Outside clicks cancel the unconfirmed inline edit. Never leave a detached
      // input as a permanent editing lock or persist a temporary node silently.
      if (inlineName !== input || input.disabled) return;
      inlineName = null; graph = structuredClone(lastSavedGraph); render();
    });
    input.addEventListener('pointerdown', event => event.stopPropagation());
    input.addEventListener('keydown', async event => {
      if (event.isComposing || !['Enter', 'Escape'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      if (event.key === 'Escape') { inlineName = null; graph = structuredClone(lastSavedGraph); render(); viewport.focus(); return; }
      if (!input.value.trim()) { input.setCustomValidity('Node name is required.'); input.reportValidity(); return; }
      const changed = structuredClone(graph);
      changed.nodes.find(candidate => candidate.id === node.id).name = input.value.trim();
      input.disabled = true;
      try {
        const oldIds = new Set(lastSavedGraph.nodes.map(candidate => candidate.id));
        await commitDocument(changed, label);
        const saved = node.id > 0 ? nodeById(node.id) : graph.nodes.find(candidate => !oldIds.has(candidate.id));
        inlineName = null; selection = saved ? {kind: 'node', id: saved.id} : null; selectedNodes = new Set(saved ? [saved.id] : []); render(); viewport.focus();
      } catch (error) { inlineName = null; showStatus(error.message, true); render(); }
    });
  };
  const quickNode = mode => {
    if (mutationBusy || inlineName) return;
    const source = selection?.kind === 'node' ? nodeById(selection.id) : null;
    if (mode && !source) return;
    const point = mode ? {x: source.x + (mode === 'connected' ? NODE_WIDTH + 70 : 0), y: source.y + (mode === 'nearby' ? NODE_HEIGHT + 24 : 0)} : (pointerPosition || spawnPosition('node'));
    const document = structuredClone(graph);
    const node = {id: temporaryId--, name: 'New node', description: '', external_url: '', color: source?.color && PALETTE.some(([color]) => color === source.color.toUpperCase()) ? source.color : '#3B82F6', group_id: source?.group_id || groupAtPoint(point.x, point.y)?.id || null, keep_position: false, ...point};
    while (document.nodes.some(other => Math.abs(other.x - node.x) < NODE_WIDTH + 12 && Math.abs(other.y - node.y) < NODE_HEIGHT + 12)) node.y += NODE_HEIGHT + 24;
    document.nodes.push(node);
    if (mode === 'connected') document.edges.push({id: temporaryId--, source_id: source.id, target_id: node.id, direction: 'forward', line_style: 'solid'});
    growGroups(document);
    renameInline(node, document, 'Quick add node');
  };

  const arrangeSelection = async mode => {
    if (mutationBusy || inlineName || cancelActiveGesture || activeConnection || activeGroupDraw) return;
    const document = structuredClone(graph);
    let nodes = document.nodes.filter(node => selectedNodeIds().has(node.id) && !node.keep_position);
    if (mode === 'branch') {
      const first = selection?.kind === 'node' ? document.nodes.find(node => node.id === selection.id) : null;
      if (!first) return;
      const visited = new Set([first.id]); const queue = [{node: first, depth: 0}]; const levels = new Map();
      while (queue.length) {
        const {node, depth} = queue.shift();
        const row = levels.get(depth) || 0; levels.set(depth, row + 1);
        if (!node.keep_position) { node.x = first.x + depth * 270; node.y = first.y + row * 110; }
        for (const edge of document.edges.filter(edge => edge.source_id === node.id && edge.direction !== 'none')) {
          if (!visited.has(edge.target_id)) { visited.add(edge.target_id); const child = document.nodes.find(item => item.id === edge.target_id); if (child) queue.push({node: child, depth: depth + 1}); }
        }
      }
    } else {
      if (nodes.length < 2) return;
      const minX = Math.min(...nodes.map(node => node.x)), maxX = Math.max(...nodes.map(node => node.x));
      const minY = Math.min(...nodes.map(node => node.y)), maxY = Math.max(...nodes.map(node => node.y));
      nodes.sort((a, b) => mode === 'vertical' ? a.y - b.y : a.x - b.x);
      nodes.forEach((node, index) => {
        if (['left', 'center', 'right'].includes(mode)) node.x = mode === 'left' ? minX : mode === 'right' ? maxX : (minX + maxX) / 2;
        if (['top', 'middle', 'bottom'].includes(mode)) node.y = mode === 'top' ? minY : mode === 'bottom' ? maxY : (minY + maxY) / 2;
        if (mode === 'horizontal') node.x = minX + (maxX - minX) * index / (nodes.length - 1);
        if (mode === 'vertical') node.y = minY + (maxY - minY) * index / (nodes.length - 1);
      });
    }
    try { await commitDocument(document, mode === 'branch' ? 'Arrange branch Right' : `Align ${mode}`); } catch (error) { showStatus(error.message, true); }
  };

  const shortcutDefaults = {
    create: ['N', 'Create node at pointer'], connected: ['Ctrl+Enter', 'Create connected node'], nearby: ['Shift+Enter', 'Create nearby node'],
    edit: ['E', 'Edit selected node / group'], rename: ['F2', 'Rename inline'], area: ['R', 'Area selection mode'], all: ['Ctrl+A', 'Select visible objects'],
    copy: ['Ctrl+C', 'Copy selection'], paste: ['Ctrl+V', 'Paste cluster'], duplicate: ['Shift+D', 'Duplicate selection'], remove: ['Delete', 'Delete selection'],
    undo: ['Ctrl+Z', 'Undo'], redo: ['Ctrl+Shift+Z', 'Redo'], group: ['B', 'Draw group'], collapse: ['Shift+B', 'Collapse / expand group'],
    connect: ['C', 'Connect node'], confirm: ['Enter', 'Confirm connection'], line: ['L', 'Toggle solid / dashed'], arrow: ['A', 'Cycle arrow direction'], link: ['O', 'Open node link'],
    pan: ['Space', 'Hold to pan'], zoomIn: ['+', 'Zoom in'], zoomOut: ['-', 'Zoom out'], zoomReset: ['0', 'Zoom 100%'], fit: ['F', 'Fit map'], find: ['/', 'Search nodes'],
    layout: ['J', 'Automatic layout'], branch: ['Shift+J', 'Arrange branch Right'], pin: ['Shift+P', 'Keep position'], fitResults: ['Shift+F', 'Fit search results'],
    nextResult: ['F3', 'Next result'], previousResult: ['Alt+F3', 'Previous result'], newMap: ['M', 'New map'], editMap: ['Shift+M', 'Edit map'],
    history: ['H', 'Map history'], trash: ['T', 'Map trash'], shortcuts: ['K', 'Shortcut settings'],
  };
  const shortcutStorage = `mmp-system-map-shortcuts:${root.dataset.userId || 'user'}`;
  let shortcutOverrides = {};
  try { shortcutOverrides = JSON.parse(localStorage.getItem(shortcutStorage) || '{}'); } catch (_) { /* Defaults remain usable when storage is unavailable. */ }
  if (!shortcutOverrides || Array.isArray(shortcutOverrides) || typeof shortcutOverrides !== 'object') shortcutOverrides = {};
  const keyCombination = event => {
    let key = event.key === ' ' ? 'Space' : event.key.length === 1 ? event.key.toUpperCase() : event.key;
    if (['Control', 'Meta', 'Alt', 'Shift', 'Dead', 'Unidentified'].includes(key)) return '';
    return [event.ctrlKey || event.metaKey ? 'Ctrl' : '', event.altKey ? 'Alt' : '', event.shiftKey && !['+', '?'].includes(key) ? 'Shift' : '', key].filter(Boolean).join('+');
  };
  const reservedShortcut = key => /^(?:Ctrl\+(?:W|L|T|N|R|Q)|Ctrl\+Shift\+(?:W|T|N)|Alt\+F4|Tab|Shift\+Tab|Escape)$/.test(key) || /Arrow(?:Left|Right|Up|Down)$/.test(key);
  for (const [id, key] of Object.entries(shortcutOverrides)) {
    if (!(id in shortcutDefaults) || typeof key !== 'string' || key.length > 50 || reservedShortcut(key)) delete shortcutOverrides[id];
  }
  const shortcutDialog = root.querySelector('[data-shortcut-dialog]');
  const openShortcuts = () => {
    const list = root.querySelector('[data-shortcut-list]');
    list.replaceChildren(...Object.entries(shortcutDefaults).map(([id, [key, label]]) => {
      const row = document.createElement('label');
      const title = document.createElement('span'); title.textContent = label;
      const input = document.createElement('input'); input.type = 'text'; input.readOnly = true; input.value = shortcutOverrides[id] || key;
      input.dataset.shortcutBinding = id; input.setAttribute('aria-label', `Shortcut for ${label}`);
      row.append(title, input); return row;
    }));
    root.querySelector('[data-shortcut-error]').textContent = '';
    shortcutDialog.showModal();
  };
  const selectedNode = () => selection?.kind === 'node' ? nodeById(selection.id) : null;
  const removeSelection = async () => {
    if (mutationBusy || inlineName || cancelActiveGesture || activeConnection || activeGroupDraw) return;
    if (selection?.kind === 'edge') { await deleteEntity('edge', selection.id); return; }
    if (!selectedNodes.size && !selectedGroups.size) return;
    const document = structuredClone(graph);
    // Deleting a group preserves its members unless those nodes are explicitly
    // selected too. Copy and move include implicit group members exactly once.
    document.groups = document.groups.filter(group => !selectedGroups.has(group.id));
    document.nodes = document.nodes.filter(node => !selectedNodes.has(node.id));
    document.nodes.forEach(node => { if (selectedGroups.has(node.group_id)) node.group_id = null; });
    document.edges = document.edges.filter(edge => !selectedNodes.has(edge.source_id) && !selectedNodes.has(edge.target_id));
    await commitDocument(document, 'Delete selection');
    selectedNodes.clear(); selectedGroups.clear(); selection = null; hideFloatingControls(); render(); viewport.focus({preventScroll:true});
  };
  const runCommand = async command => {
    if (cancelActiveGesture || activeConnection || activeGroupDraw) return;
    lastEntityClick = null;
    if (command === 'shortcuts') { openShortcuts(); return; }
    if (command === 'find') { root.querySelector('[data-node-search]').focus(); return; }
    if (command === 'nextResult' || command === 'previousResult') { visitSearch(command === 'previousResult'); return; }
    if (command === 'fitResults') { fitNodes(matchedNodes()); return; }
    if (command === 'pan') { spaceHeld = true; return; }
    if (command === 'zoomIn' || command === 'zoomOut' || command === 'zoomReset') {
      const previous = scale; scale = command === 'zoomReset' ? 1 : clamp(scale * (command === 'zoomIn' ? 1.15 : 1 / 1.15), .02, 2);
      panX = viewport.clientWidth / 2 - (viewport.clientWidth / 2 - panX) * scale / previous;
      panY = viewport.clientHeight / 2 - (viewport.clientHeight / 2 - panY) * scale / previous;
      applyTransform(); return;
    }
    if (command === 'fit') { root.querySelector('[data-fit-map]').click(); return; }
    if (command === 'undo' || command === 'redo') { await historyStep(command === 'redo'); return; }
    if (command === 'copy') { copySelection(); return; }
    if (command === 'paste' || command === 'duplicate') { await pasteSelection(command === 'duplicate'); return; }
    if (mutationBusy || inlineName) return;
    if (command === 'create') quickNode();
    if (command === 'connected' || command === 'nearby') quickNode(command);
    if (command === 'edit' && selection && ['node', 'group'].includes(selection.kind)) openEntityDialog(selection.kind, selectedItem());
    if (command === 'rename') renameInline(selectedNode());
    if (command === 'newMap') openMapDialog(false);
    if (command === 'editMap') openMapDialog(true);
    if (command === 'history') root.querySelector('a[href*="/history/"]')?.click();
    if (command === 'trash') root.querySelector('a[href*="/trash/"]')?.click();
    if (command === 'area') { selectingArea = true; viewport.style.cursor = 'crosshair'; showStatus('Drag empty canvas to select nodes; Esc to cancel'); }
    if (command === 'all') { selectedNodes = new Set(graph.nodes.filter(nodeAvailable).map(node => node.id)); selectedGroups = new Set(queryText() ? [] : graph.groups.map(group => group.id)); selection = selectedNodes.size ? {kind: 'node', id: [...selectedNodes][0]} : selectedGroups.size ? {kind: 'group', id: [...selectedGroups][0]} : null; hideFloatingControls({keepSelection:true}); render(); }
    if (command === 'group') { root.querySelector('[data-context-create="group"]').click(); }
    if (command === 'collapse' && selectedGroups.size) {
      const document = structuredClone(graph); document.groups.filter(item => selectedGroups.has(item.id)).forEach(group => { group.is_collapsed = !group.is_collapsed; });
      await commitDocument(document, 'Toggle group');
    }
    if (command === 'pin' && selectedNodeIds().size) {
      const document = structuredClone(graph); const selected = document.nodes.filter(node => selectedNodeIds().has(node.id)); const keep = !selected.every(node => node.keep_position);
      selected.forEach(node => { node.keep_position = keep; }); await commitDocument(document, keep ? 'Keep node positions' : 'Release node positions');
    }
    if (command === 'layout') { root.querySelector('.map-more-menu').open=true;root.querySelector('[data-layout-select]').focus(); }
    if (command === 'branch') await arrangeSelection('branch');
    if (command === 'connect' && selectedNode()) armKeyboardConnection(selectedNode().id);
    if (command === 'confirm' && keyboardConnectionSourceId && selectedNode() && keyboardConnectionSourceId !== selectedNode().id) await quickConnect(keyboardConnectionSourceId, selectedNode().id, 0, 0, true);
    if (command === 'line' && selection?.kind === 'edge') await updateSelectedEdge({line_style: selectedItem().line_style === 'dashed' ? 'solid' : 'dashed'});
    if (command === 'arrow' && selection?.kind === 'edge') { const directions = ['none', 'forward', 'bidirectional']; await updateSelectedEdge({direction: directions[(directions.indexOf(selectedItem().direction) + 1) % 3]}); }
    if (command === 'link' && selectedNode()?.external_url) window.open(selectedNode().external_url, '_blank', 'noopener,noreferrer');
    if (command === 'remove' && selection) {
      await removeSelection();
    }
  };

  root.addEventListener('click', event => {
    const button = event.target.closest('[data-map-command]');
    if (button) {
      event.preventDefault();
      const operation = button.matches('[data-context-paste]') ? pasteSelection(false, contextSurfacePoint) : runCommand(button.dataset.mapCommand);
      Promise.resolve(operation).catch(error => showStatus(error.message, true));
      root.querySelector('.map-more-menu')?.removeAttribute('open');
      [mapContextMenu, entityContext].forEach(menu => { menu.hidden = true; });
      if (button.closest('[data-entity-context],[data-map-context-menu]') && !['edit','shortcuts'].includes(button.dataset.mapCommand)) viewport.focus({preventScroll:true});
    }
    if (event.target.closest('[data-close-node-details]')) { detailsDismissed = true; renderNodeDetails(); viewport.focus(); }
    if (event.target.closest('[data-toggle-minimap]')) { const minimapButton = root.querySelector('[data-overview-fit]'); minimapButton.hidden = !minimapButton.hidden; event.target.closest('button').setAttribute('aria-pressed', String(!minimapButton.hidden)); }
  });
  root.querySelector('[data-arrange-select]').addEventListener('change', event => { const mode = event.target.value; event.target.value = ''; if (mode) arrangeSelection(mode); });
  root.querySelector('[data-shortcut-reset]').addEventListener('click', () => {
    root.querySelectorAll('[data-shortcut-binding]').forEach(input => { input.value = shortcutDefaults[input.dataset.shortcutBinding][0]; });
    root.querySelector('[data-shortcut-error]').textContent = '';
  });
  root.querySelector('[data-shortcut-form]').addEventListener('submit', event => {
    event.preventDefault();
    const overrides = {}, used = new Set();
    for (const input of root.querySelectorAll('[data-shortcut-binding]')) {
      if (!input.value || used.has(input.value) || reservedShortcut(input.value)) { root.querySelector('[data-shortcut-error]').textContent = 'Choose unique shortcuts; browser-reserved combinations cannot be used.'; return; }
      used.add(input.value); overrides[input.dataset.shortcutBinding] = input.value;
    }
    try { localStorage.setItem(shortcutStorage, JSON.stringify(overrides)); shortcutOverrides = overrides; shortcutDialog.close(); showStatus('Shortcuts saved for this user and browser'); }
    catch (_) { root.querySelector('[data-shortcut-error]').textContent = 'Browser storage is unavailable; defaults are still active.'; }
  });
  root.addEventListener('keydown', event => {
    if (event.isComposing || event.repeat || event.keyCode === 229) return;
    if (event.target.matches('[data-shortcut-binding]')) {
      if (event.key === 'Tab' || event.key === 'Escape') return;
      event.preventDefault(); event.stopImmediatePropagation(); const key = keyCombination(event);
      if (!key) return;
      if (reservedShortcut(key)) { root.querySelector('[data-shortcut-error]').textContent = 'This combination is reserved for the browser.'; return; }
      event.target.value = key; return;
    }
    const typing = event.target.closest('input,textarea,select,[contenteditable="true"]');
    if (typing) {
      if (event.target.matches('[data-node-search]') && ['Enter', 'F3'].includes(event.key)) { event.preventDefault(); event.stopImmediatePropagation(); visitSearch(event.shiftKey || event.altKey); }
      return;
    }
    if (mapDialog.open || entityDialog.open || shortcutDialog.open) return;
    // Menu controls keep their native activation/navigation. Canvas shortcuts
    // only apply to the canvas, not focused buttons or popover radio options.
    if (event.target.closest('button,a,summary,[role="menu"]') && event.key !== 'Escape' && !event.ctrlKey && !event.metaKey) return;
    if (event.key === 'Escape') {
      cancelActiveGesture?.();
      activeConnection?.cancel?.();
      lastEntityClick = null;
      selectingArea = false; spaceHeld = false; viewport.style.cursor = ''; selection = null; selectedNodes.clear(); selectedGroups.clear(); cancelGroupDrawing(); cancelKeyboardConnection(); hideFloatingControls(); render();
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    if (cancelActiveGesture || activeConnection || activeGroupDraw) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) && !event.target.closest('[data-group-resize]')) {
      event.preventDefault(); event.stopImmediatePropagation();
      const horizontal = event.key === 'ArrowLeft' || event.key === 'ArrowRight'; const sign = ['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1;
      if (event.altKey && selectedNodes.size && !mutationBusy) {
        const document = structuredClone(graph); document.nodes.filter(node => selectedNodes.has(node.id)).forEach(node => { node[horizontal ? 'x' : 'y'] += sign * 10; });
        commitDocument(document, 'Move selected nodes').catch(error => showStatus(error.message, true)); return;
      }
      const current = selectedNode(); const candidates = graph.nodes.filter(node => nodeAvailable(node) && (!current || (node[horizontal ? 'x' : 'y'] - current[horizontal ? 'x' : 'y']) * sign > 0));
      candidates.sort((a, b) => current ? Math.hypot(a.x - current.x, a.y - current.y) - Math.hypot(b.x - current.x, b.y - current.y) : a.y - b.y || a.x - b.x);
      const next = candidates[0]; if (next) { selectedNodes = event.shiftKey ? new Set([...selectedNodes, next.id]) : new Set([next.id]); if (!event.shiftKey) selectedGroups.clear(); selection = {kind: 'node', id: next.id}; detailsDismissed = false; render(); } return;
    }
    const key = keyCombination(event);
    if (event.key === 'Enter' && keyboardConnectionSourceId && event.target.closest('[data-node-id]')) {
      event.preventDefault(); event.stopImmediatePropagation();
      quickConnect(keyboardConnectionSourceId, event.target.closest('[data-node-id]').dataset.nodeId, 0, 0, true); return;
    }
    if (event.key === 'Enter' && event.target.closest('[data-node-id],[data-group-id],[data-edge-id]')) return;
    const entry = Object.entries(shortcutDefaults).find(([id, [defaultKey]]) => (shortcutOverrides[id] || defaultKey) === key);
    if (!entry) return;
    event.preventDefault(); event.stopImmediatePropagation(); runCommand(entry[0]).catch(error => showStatus(error.message, true));
  }, true);
  document.addEventListener('keyup', event => { if (event.key === ' ' || keyCombination(event).split('+').at(-1) === (shortcutOverrides.pan || shortcutDefaults.pan[0]).split('+').at(-1)) spaceHeld = false; });
  window.addEventListener('blur', () => { spaceHeld = false; });
  window.addEventListener('beforeunload', event => { if (inlineName || mutationBusy) { event.preventDefault(); event.returnValue = ''; } });

  viewport.addEventListener('pointermove', event => { pointerPosition = clientToSurface(event.clientX, event.clientY); });
  // Right-drag pans the camera; a stationary right-click opens the menu.
  // Pointer capture makes this work over nodes as well as the empty canvas.
  viewport.addEventListener('pointerdown', event => {
    if (event.button !== 2 || event.isPrimary === false || event.target.closest('button,input,textarea,a,[contenteditable="true"]')) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (inlineName || cancelActiveGesture || drawingGroup) return;
    const target = event.target, start = {x: event.clientX, y: event.clientY}, oldPan = {x: panX, y: panY};
    let moved = false;
    lastEntityClick = null; hideFloatingControls({keepSelection: true});
    viewport.focus({preventScroll: true}); viewport.setPointerCapture(event.pointerId);
    const cleanup = () => {
      viewport.removeEventListener('pointermove', move); viewport.removeEventListener('pointerup', stop); viewport.removeEventListener('pointercancel', stop);
      window.removeEventListener('blur', cancel);
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
      viewport.style.cursor = ''; cancelActiveGesture = null; suppressContextUntil = performance.now() + 600;
    };
    const move = current => {
      if (current.pointerId !== event.pointerId) return;
      moved ||= Math.hypot(current.clientX - start.x, current.clientY - start.y) > 5;
      if (!moved) return;
      viewport.style.cursor = 'grabbing';
      panX = oldPan.x + current.clientX - start.x; panY = oldPan.y + current.clientY - start.y; applyTransform();
    };
    const cancel = () => { cleanup(); panX = oldPan.x; panY = oldPan.y; applyTransform(); };
    const stop = current => {
      if (current.pointerId !== event.pointerId) return;
      if (current.type === 'pointercancel') { cancel(); return; }
      move(current); cleanup(); suppressClickUntil = performance.now() + 300;
      if (!moved) showCanvasContext({target, clientX: current.clientX, clientY: current.clientY});
    };
    cancelActiveGesture = cancel;
    viewport.addEventListener('pointermove', move); viewport.addEventListener('pointerup', stop); viewport.addEventListener('pointercancel', stop);
    window.addEventListener('blur', cancel);
  }, true);
  nodeLayer.addEventListener('dblclick', event => {
    const node = event.target.closest('[data-node-id]');
    if (!node || mutationBusy || cancelActiveGesture || inlineName || entityDialog.open) return;
    event.preventDefault(); lastEntityClick = null;
    openEntityDialog('node', nodeById(node.dataset.nodeId));
  });
  viewport.addEventListener('pointerdown', event => {
    if (inlineName && !event.target.closest('.map-inline-name')) {
      event.preventDefault();event.stopImmediatePropagation();inlineName=null;graph=structuredClone(lastSavedGraph);render();viewport.focus({preventScroll:true});return;
    }
    if (event.button !== 0 || event.isPrimary === false || event.target.closest('[data-connect-handle],[data-group-resize],button,input,a') || inlineName || cancelActiveGesture || drawingGroup) return;
    const nodeElement = event.target.closest('[data-node-id]');
    const groupHeader = event.target.closest('.relationship-group-header');
    const element = nodeElement || groupHeader?.closest('[data-group-id]');
    const kind = nodeElement ? 'node' : 'group';
    const id = element ? Number(nodeElement ? element.dataset.nodeId : element.dataset.groupId) : null;
    if (keyboardConnectionSourceId && nodeElement) {
      event.preventDefault(); event.stopImmediatePropagation(); suppressClickUntil = performance.now() + 400;
      quickConnect(keyboardConnectionSourceId, id, event.clientX, event.clientY); return;
    }
    const empty = !element && !event.target.closest('[data-edge-id],button,input,a');
    if (event.shiftKey && element) {
      event.preventDefault(); event.stopImmediatePropagation();
      lastEntityClick=null;
      const set = kind === 'node' ? selectedNodes : selectedGroups;
      if (set.has(id)) set.delete(id); else set.add(id);
      selection = selectedNodes.size ? {kind: 'node', id: [...selectedNodes].at(-1)} : selectedGroups.size ? {kind:'group',id:[...selectedGroups].at(-1)} : null;
      suppressClickUntil = performance.now() + 400; hideFloatingControls({keepSelection:true}); render(); return;
    }
    if (!(element || spaceHeld || empty)) return;
    if (mutationBusy) { event.preventDefault(); event.stopImmediatePropagation(); return; }
    event.preventDefault(); event.stopImmediatePropagation(); viewport.focus({preventScroll: true});
    const original = structuredClone(graph); const start = {x: event.clientX, y: event.clientY}; const startWorld = clientToSurface(start.x, start.y);
    const pan = spaceHeld;
    const area = !pan && empty; const oldPan = {x: panX, y: panY};
    if (!pan && element && !(kind === 'node' ? selectedNodeIds().has(id) : selectedGroups.has(id))) {
      selectedNodes = new Set(kind === 'node' ? [id] : []); selectedGroups = new Set(kind === 'group' ? [id] : []);
    }
    const initialNodes = new Set(event.shiftKey ? selectedNodes : []), initialGroups = new Set(event.shiftKey ? selectedGroups : []);
    const ids = selectedNodeIds(), groupIds = new Set(selectedGroups); let moved = false;
    hideFloatingControls({keepSelection:true});
    viewport.setPointerCapture(event.pointerId);
    const clearGuides = () => { surface.querySelectorAll('.map-snap-guide').forEach(item => item.remove()); };
    const move = current => {
      if (current.pointerId !== event.pointerId) return;
      let dx = (current.clientX - start.x) / scale, dy = (current.clientY - start.y) / scale;
      moved ||= Math.hypot(current.clientX - start.x, current.clientY - start.y) > 4;
      if (!moved) return;
      if (pan) { panX = oldPan.x + current.clientX - start.x; panY = oldPan.y + current.clientY - start.y; applyTransform(); return; }
      if (area) {
        detailsDismissed = true;
        const end = clientToSurface(current.clientX, current.clientY), bounds = viewport.getBoundingClientRect(), workspaceBounds = workspace.getBoundingClientRect();
        groupDrawPreview.hidden = false; groupDrawPreview.style.cssText = `left:${bounds.left - workspaceBounds.left + Math.min(start.x - bounds.left, current.clientX - bounds.left)}px;top:${bounds.top - workspaceBounds.top + Math.min(start.y - bounds.top, current.clientY - bounds.top)}px;width:${Math.abs(current.clientX - start.x)}px;height:${Math.abs(current.clientY - start.y)}px`;
        const left = Math.min(startWorld.x,end.x), right = Math.max(startWorld.x,end.x), top = Math.min(startWorld.y,end.y), bottom = Math.max(startWorld.y,end.y);
        selectedGroups = new Set([...initialGroups, ...original.groups.filter(group => !queryText() && group.x >= left && group.x + group.width <= right && group.y >= top && group.y + (group.is_collapsed ? COLLAPSED_GROUP_HEIGHT : group.height) <= bottom).map(group=>group.id)]);
        selectedNodes = new Set([...initialNodes, ...original.nodes.filter(node => nodeAvailable(node) && node.x <= right && node.x + NODE_WIDTH >= left && node.y <= bottom && node.y + NODE_HEIGHT >= top).map(node => node.id)]);
        selection = selectedNodes.size ? {kind: 'node', id: [...selectedNodes][0]} : selectedGroups.size ? {kind:'group',id:[...selectedGroups][0]} : null; render(); return;
      }
      const anchor = original.nodes.find(node => node.id === id);
      clearGuides();
      if (!current.altKey && anchor && !groupIds.size) {
        const others = original.nodes.filter(node => !ids.has(node.id) && !isNodeCollapsed(node)); const distance = 6 / scale;
        for (const axis of ['x', 'y']) {
          const offsets = axis === 'x' ? [0, NODE_WIDTH / 2, NODE_WIDTH] : [0, NODE_HEIGHT / 2, NODE_HEIGHT];
          const requested = axis === 'x' ? dx : dy; let best = null;
          for (const other of others) for (const offset of offsets) for (const otherOffset of offsets) {
            const adjustment = other[axis] + otherOffset - (anchor[axis] + offset + requested);
            if (Math.abs(adjustment) <= distance && (!best || Math.abs(adjustment) < Math.abs(best.adjustment))) best = {adjustment, line: other[axis] + otherOffset};
          }
          if (best) {
            if (axis === 'x') dx += best.adjustment; else dy += best.adjustment;
            const guide = document.createElement('i'); guide.className = `map-snap-guide map-snap-${axis}`; guide.style[axis === 'x' ? 'left' : 'top'] = `${best.line}px`; surface.append(guide);
          }
        }
      }
      graph.nodes.forEach(node => { const previous = original.nodes.find(item => item.id === node.id); if (ids.has(node.id)) { node.x = previous.x + dx; node.y = previous.y + dy; const rendered = nodeLayer.querySelector(`[data-node-id="${node.id}"]`); if (rendered) { rendered.style.left = `${node.x}px`; rendered.style.top = `${node.y}px`; } } });
      graph.groups.forEach(group => { if (groupIds.has(group.id)) { const previous=original.groups.find(item=>item.id===group.id); group.x=previous.x+dx;group.y=previous.y+dy;const rendered=groupLayer.querySelector(`[data-group-id="${group.id}"]`);if(rendered){rendered.style.left=`${group.x}px`;rendered.style.top=`${group.y}px`;} } });
      renderEdges(); renderMinimap();
    };
    const stop = async current => {
      if (current.pointerId !== event.pointerId) return;
      viewport.removeEventListener('pointermove', move); viewport.removeEventListener('pointerup', stop); viewport.removeEventListener('pointercancel', stop); clearGuides(); groupDrawPreview.hidden = true; selectingArea = false; viewport.style.cursor = '';
      cancelActiveGesture = null;
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
      if (current.type === 'pointercancel') { graph = original; render(); return; }
      if (moved) suppressClickUntil = performance.now() + 300;
      if (area || pan) { if (!moved && area && !event.shiftKey) { hideFloatingControls(); render(); } return; }
      selection = {kind, id};
      if (!moved) {
        detailsDismissed = false;
        suppressClickUntil = performance.now() + 300;
        if (lastEntityClick?.kind === kind && lastEntityClick.id === id && performance.now() - lastEntityClick.at < 500) {
          lastEntityClick = null;
          if (kind==='node') openEntityDialog('node',nodeById(id));
          else { const changed=structuredClone(graph);const group=changed.groups.find(item=>item.id===id);group.is_collapsed=!group.is_collapsed;try{await commitDocument(changed,'Toggle group');}catch(error){showStatus(error.message,true);} }
          return;
        }
        lastEntityClick = {kind, id, at: performance.now()};
        const bounds = element.getBoundingClientRect(); showEntityToolbar(kind,id,bounds.left,bounds.bottom); return;
      }
      lastEntityClick = null;
      const changed = structuredClone(graph);
      changed.nodes.filter(node => ids.has(node.id) && !groupIds.has(node.group_id)).forEach(node => { const target = [...changed.groups].reverse().find(group => !group.is_collapsed && node.x + NODE_WIDTH / 2 >= group.x && node.x + NODE_WIDTH / 2 <= group.x + group.width && node.y + NODE_HEIGHT / 2 >= group.y && node.y + NODE_HEIGHT / 2 <= group.y + group.height); node.group_id = target?.id || null; });
      try { await commitDocument(changed, 'Move selected nodes'); } catch (error) { showStatus(error.message, true); render(); }
    };
    cancelActiveGesture = () => {
      viewport.removeEventListener('pointermove', move); viewport.removeEventListener('pointerup', stop); viewport.removeEventListener('pointercancel', stop);
      clearGuides(); groupDrawPreview.hidden = true; selectingArea = false; viewport.style.cursor = ''; graph = structuredClone(original);
      if (pan) { panX=oldPan.x;panY=oldPan.y;applyTransform(); }
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
      cancelActiveGesture = null; suppressClickUntil = performance.now() + 300; render();
    };
    viewport.addEventListener('pointermove', move); viewport.addEventListener('pointerup', stop); viewport.addEventListener('pointercancel', stop);
  }, true);

  viewport.addEventListener('pointerdown', event => {
    const groupElement = event.target.closest('[data-group-id]');
    const resizeHandle = event.target.closest('[data-group-resize]');
    if (!groupElement || (!resizeHandle && !event.target.closest('.relationship-group-header')) || event.button !== 0 || event.isPrimary === false || spaceHeld || mutationBusy || cancelActiveGesture || inlineName || activeConnection || activeGroupDraw) return;
    event.preventDefault(); event.stopImmediatePropagation(); viewport.focus({preventScroll: true});
    const id = Number(groupElement.dataset.groupId), original = structuredClone(graph), group = original.groups.find(item => item.id === id);
    const start = {x: event.clientX, y: event.clientY}; const direction = resizeHandle?.dataset.resizeDirection || 'se';
    let moved = false;
    viewport.setPointerCapture(event.pointerId);
    const move = current => {
      if (current.pointerId !== event.pointerId) return;
      const dx = (current.clientX - start.x) / scale, dy = (current.clientY - start.y) / scale;
      moved ||= Math.hypot(current.clientX - start.x, current.clientY - start.y) > 4;
      if (!moved) return;
      const next = structuredClone(original), target = next.groups.find(item => item.id === id);
      if (resizeHandle) {
        const west = direction.includes('w'), north = direction.includes('n');
        target.x = west ? Math.min(group.x + dx, group.x + group.width - 220) : group.x;
        target.y = north ? Math.min(group.y + dy, group.y + group.height - 140) : group.y;
        target.width = Math.max(220, group.width + (west ? group.x - target.x : direction.includes('e') ? dx : 0));
        target.height = Math.max(140, group.height + (north ? group.y - target.y : direction.includes('s') ? dy : 0));
        growGroups(next);
      } else {
        target.x += dx; target.y += dy;
        next.nodes.filter(node => node.group_id === id).forEach(node => { node.x += dx; node.y += dy; });
      }
      graph = next; render();
    };
    const cleanup = () => {
      viewport.removeEventListener('pointermove', move); viewport.removeEventListener('pointerup', stop); viewport.removeEventListener('pointercancel', stop);
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
      cancelActiveGesture = null;
    };
    const stop = async current => {
      if (current.pointerId !== event.pointerId) return;
      cleanup();
      if (current.type === 'pointercancel') { graph = original; render(); return; }
      selection = {kind: 'group', id}; selectedNodes.clear();
      if (!moved) {
        suppressClickUntil = performance.now() + 300;
        if (lastEntityClick?.kind === 'group' && lastEntityClick.id === id && performance.now() - lastEntityClick.at < 500) {
          lastEntityClick = null; const document = structuredClone(graph); const target = document.groups.find(item => item.id === id); target.is_collapsed = !target.is_collapsed;
          try { await commitDocument(document, 'Toggle group'); } catch (error) { showStatus(error.message, true); } return;
        }
        lastEntityClick = {kind: 'group', id, at: performance.now()}; showEntityToolbar('group', id, current.clientX, current.clientY); return;
      }
      lastEntityClick = null;
      suppressClickUntil = performance.now() + 300;
      try { await commitDocument(structuredClone(graph), resizeHandle ? 'Resize group' : 'Move group and nodes'); } catch (error) { showStatus(error.message, true); render(); }
    };
    cancelActiveGesture = () => { cleanup(); graph = structuredClone(original); suppressClickUntil = performance.now() + 300; render(); };
    viewport.addEventListener('pointermove', move); viewport.addEventListener('pointerup', stop); viewport.addEventListener('pointercancel', stop);
  }, true);

  viewport.addEventListener('pointerdown', event => {
    const nodeElement = event.target.closest('[data-node-id]');
    const groupElement = event.target.closest('[data-group-id]');
    const resizeHandle = event.target.closest('[data-group-resize]');
    const connectHandle = event.target.closest('[data-connect-handle]');

    // Right-click is reserved for context menus and must never start a drag,
    // resize, connection, or canvas pan operation.
    if (event.button !== 0 || event.isPrimary === false || cancelActiveGesture || inlineName || activeConnection || activeGroupDraw || mutationBusy) return;

    if (drawingGroup && !nodeElement && !groupElement) {
      event.preventDefault();
      const startClient = {x: event.clientX, y: event.clientY};
      const startSurface = clientToSurface(event.clientX, event.clientY);
      let currentSurface = startSurface;
      let moved = false;
      viewport.setPointerCapture(event.pointerId);
      groupDrawPreview.hidden = false;
      const updatePreview = moveEvent => {
        currentSurface = clientToSurface(moveEvent.clientX, moveEvent.clientY);
        moved = moved || Math.hypot(moveEvent.clientX - startClient.x, moveEvent.clientY - startClient.y) > 4;
        const workspaceBounds = workspace.getBoundingClientRect();
        groupDrawPreview.style.left = `${Math.min(startClient.x, moveEvent.clientX) - workspaceBounds.left}px`;
        groupDrawPreview.style.top = `${Math.min(startClient.y, moveEvent.clientY) - workspaceBounds.top}px`;
        groupDrawPreview.style.width = `${Math.abs(moveEvent.clientX - startClient.x)}px`;
        groupDrawPreview.style.height = `${Math.abs(moveEvent.clientY - startClient.y)}px`;
      };
      const stopDrawing = stopEvent => {
        viewport.removeEventListener('pointermove', updatePreview);
        viewport.removeEventListener('pointerup', stopDrawing);
        viewport.removeEventListener('pointercancel', stopDrawing);
        if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
        activeGroupDraw = null;
        groupDrawPreview.hidden = true;
        viewport.classList.remove('is-drawing-group');
        connectionHint.hidden = true;
        drawingGroup = false;
        if (stopEvent.type !== 'pointerup') return;
        const rawX = moved ? Math.min(startSurface.x, currentSurface.x) : startSurface.x;
        const rawY = moved ? Math.min(startSurface.y, currentSurface.y) : startSurface.y;
        const width = moved ? Math.max(MIN_GROUP_WIDTH, Math.abs(currentSurface.x - startSurface.x)) : 360;
        const height = moved ? Math.max(MIN_GROUP_HEIGHT, Math.abs(currentSurface.y - startSurface.y)) : 260;
        openEntityDialog('group', null, {
          x: clamp(rawX, -20000, 20000 - width),
          y: clamp(rawY, -20000, 20000 - height),
          width: Math.min(width, 8000),
          height: Math.min(height, 8000),
        });
      };
      activeGroupDraw = {cancel: () => stopDrawing({type: 'pointercancel'})};
      viewport.addEventListener('pointermove', updatePreview);
      viewport.addEventListener('pointerup', stopDrawing);
      viewport.addEventListener('pointercancel', stopDrawing);
      return;
    }

    if (mutationBusy && (nodeElement || groupElement)) {
      showStatus('Wait for the current change to finish.', true);
      return;
    }

    if (connectHandle && nodeElement) {
      event.preventDefault();
      event.stopPropagation();
      cancelKeyboardConnection();
      hideFloatingControls({keepSelection: true});
      const source = nodeById(nodeElement.dataset.nodeId);
      const preview = createSvg('path', {
        class: 'relationship-edge-preview',
        'marker-end': 'url(#map-arrow)',
      });
      edgeLayer.append(preview);
      activeConnection = {sourceId: source.id, preview};
      nodeElement.classList.add('is-connecting');
      connectionHint.textContent = 'Drop on another Node · Esc to cancel';
      connectionHint.hidden = false;
      connectHandle.setPointerCapture(event.pointerId);
      let targetElement = null;
      const move = moveEvent => {
        targetElement?.classList.remove('is-connect-target');
        const underPointer = document.elementFromPoint(moveEvent.clientX, moveEvent.clientY)?.closest('[data-node-id]');
        targetElement = underPointer && underPointer !== nodeElement ? underPointer : null;
        targetElement?.classList.add('is-connect-target');
        const bounds = viewport.getBoundingClientRect();
        const x2 = (moveEvent.clientX - bounds.left - panX) / scale;
        const y2 = (moveEvent.clientY - bounds.top - panY) / scale;
        const x1 = source.x + NODE_WIDTH / 2;
        const y1 = source.y + NODE_HEIGHT / 2;
        preview.setAttribute('d', `M ${x1} ${y1} L ${x2} ${y2}`);
      };
      const stop = async stopEvent => {
        connectHandle.removeEventListener('pointermove', move);
        connectHandle.removeEventListener('pointerup', stop);
        connectHandle.removeEventListener('pointercancel', stop);
        targetElement?.classList.remove('is-connect-target');
        nodeElement.classList.remove('is-connecting');
        preview.remove();
        activeConnection = null;
        connectionHint.hidden = true;
        suppressClickUntil = performance.now() + 250;
        if (stopEvent.type === 'pointerup' && targetElement) {
          await quickConnect(source.id, targetElement.dataset.nodeId, stopEvent.clientX, stopEvent.clientY);
        }
      };
      activeConnection.cancel = () => stop({type: 'pointercancel'});
      connectHandle.addEventListener('pointermove', move);
      connectHandle.addEventListener('pointerup', stop);
      connectHandle.addEventListener('pointercancel', stop);
      return;
    }

    if (resizeHandle && groupElement) {
      event.preventDefault();
      hideFloatingControls({keepSelection: true});
      const group = groupById(groupElement.dataset.groupId);
      const members = groupNodes(group.id);
      const startX = event.clientX;
      const startY = event.clientY;
      const startWidth = group.width;
      const startHeight = group.height;
      let moved = false;
      resizeHandle.setPointerCapture(event.pointerId);
      const move = moveEvent => {
        moved = moved || Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) > 2;
        const memberMinWidth = members.length ? Math.max(...members.map(node => node.x + NODE_WIDTH + 20 - group.x)) : MIN_GROUP_WIDTH;
        const memberMinHeight = members.length ? Math.max(...members.map(node => node.y + NODE_HEIGHT + 20 - group.y)) : MIN_GROUP_HEIGHT;
        group.width = clamp(startWidth + (moveEvent.clientX - startX) / scale, Math.max(MIN_GROUP_WIDTH, memberMinWidth), SURFACE_WIDTH - group.x);
        group.height = clamp(startHeight + (moveEvent.clientY - startY) / scale, Math.max(MIN_GROUP_HEIGHT, memberMinHeight), SURFACE_HEIGHT - group.y);
        groupElement.style.width = `${group.width}px`;
        groupElement.style.height = `${group.height}px`;
        renderMinimap();
      };
      const stop = async () => {
        resizeHandle.removeEventListener('pointermove', move);
        resizeHandle.removeEventListener('pointerup', stop);
        resizeHandle.removeEventListener('pointercancel', stop);
        if (!moved) return;
        suppressClickUntil = performance.now() + 250;
        try { await saveLayout({groups: [group]}, 'Group resized'); }
        catch (error) { showStatus(error.message, true); render(); }
      };
      resizeHandle.addEventListener('pointermove', move);
      resizeHandle.addEventListener('pointerup', stop);
      resizeHandle.addEventListener('pointercancel', stop);
      return;
    }

    if (nodeElement) {
      event.preventDefault();
      hideFloatingControls({keepSelection: true});
      const node = nodeById(nodeElement.dataset.nodeId);
      const startX = event.clientX;
      const startY = event.clientY;
      const originX = node.x;
      const originY = node.y;
      let moved = false;
      nodeElement.setPointerCapture(event.pointerId);
      const move = moveEvent => {
        moved = moved || Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) > 2;
        node.x = clamp(originX + (moveEvent.clientX - startX) / scale, 0, SURFACE_WIDTH - NODE_WIDTH);
        node.y = clamp(originY + (moveEvent.clientY - startY) / scale, 0, SURFACE_HEIGHT - NODE_HEIGHT);
        nodeElement.style.left = `${node.x}px`;
        nodeElement.style.top = `${node.y}px`;
        renderEdges();
        renderMinimap();
      };
      const stop = async () => {
        nodeElement.removeEventListener('pointermove', move);
        nodeElement.removeEventListener('pointerup', stop);
        nodeElement.removeEventListener('pointercancel', stop);
        if (!moved) return;
        suppressClickUntil = performance.now() + 250;
        const targetGroup = groupAtPoint(node.x + NODE_WIDTH / 2, node.y + NODE_HEIGHT / 2);
        node.group_id = targetGroup?.id || null;
        try { await saveLayout({nodes: [{id: node.id, x: node.x, y: node.y, group_id: node.group_id}]}, targetGroup ? `Moved to ${targetGroup.name}` : 'Node moved'); }
        catch (error) { showStatus(error.message, true); render(); }
      };
      nodeElement.addEventListener('pointermove', move);
      nodeElement.addEventListener('pointerup', stop);
      nodeElement.addEventListener('pointercancel', stop);
      return;
    }

    if (groupElement && event.target.closest('.relationship-group-header') && !event.target.closest('button')) {
      event.preventDefault();
      hideFloatingControls({keepSelection: true});
      const group = groupById(groupElement.dataset.groupId);
      const members = groupNodes(group.id);
      const origins = new Map(members.map(node => [node.id, {x: node.x, y: node.y}]));
      const startX = event.clientX;
      const startY = event.clientY;
      const originX = group.x;
      const originY = group.y;
      const leftBound = Math.min(group.x, ...members.map(node => node.x));
      const topBound = Math.min(group.y, ...members.map(node => node.y));
      const rightBound = Math.max(group.x + group.width, ...members.map(node => node.x + NODE_WIDTH));
      const bottomBound = Math.max(group.y + (group.is_collapsed ? COLLAPSED_GROUP_HEIGHT : group.height), ...members.map(node => node.y + NODE_HEIGHT));
      let moved = false;
      groupElement.setPointerCapture(event.pointerId);
      const move = moveEvent => {
        moved = moved || Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) > 2;
        const requestedX = (moveEvent.clientX - startX) / scale;
        const requestedY = (moveEvent.clientY - startY) / scale;
        const dx = clamp(requestedX, -leftBound, SURFACE_WIDTH - rightBound);
        const dy = clamp(requestedY, -topBound, SURFACE_HEIGHT - bottomBound);
        group.x = originX + dx;
        group.y = originY + dy;
        groupElement.style.left = `${group.x}px`;
        groupElement.style.top = `${group.y}px`;
        members.forEach(node => {
          const origin = origins.get(node.id);
          node.x = origin.x + dx;
          node.y = origin.y + dy;
          const element = nodeLayer.querySelector(`[data-node-id="${node.id}"]`);
          if (element) { element.style.left = `${node.x}px`; element.style.top = `${node.y}px`; }
        });
        renderEdges();
        renderMinimap();
      };
      const stop = async () => {
        groupElement.removeEventListener('pointermove', move);
        groupElement.removeEventListener('pointerup', stop);
        groupElement.removeEventListener('pointercancel', stop);
        if (!moved) return;
        suppressClickUntil = performance.now() + 250;
        try {
          await saveLayout({
            groups: [group],
            nodes: members.map(({id, x, y, group_id}) => ({id, x, y, group_id})),
          }, 'Group and Nodes moved');
        } catch (error) { showStatus(error.message, true); render(); }
      };
      groupElement.addEventListener('pointermove', move);
      groupElement.addEventListener('pointerup', stop);
      groupElement.addEventListener('pointercancel', stop);
      return;
    }

    if (event.target === viewport || event.target === surface) {
      hideFloatingControls();
      const startX = event.clientX;
      const startY = event.clientY;
      const originX = panX;
      const originY = panY;
      viewport.classList.add('is-panning');
      viewport.setPointerCapture(event.pointerId);
      const move = moveEvent => {
        panX = originX + moveEvent.clientX - startX;
        panY = originY + moveEvent.clientY - startY;
        applyTransform();
      };
      const stop = () => {
        viewport.classList.remove('is-panning');
        viewport.removeEventListener('pointermove', move);
        viewport.removeEventListener('pointerup', stop);
        viewport.removeEventListener('pointercancel', stop);
      };
      viewport.addEventListener('pointermove', move);
      viewport.addEventListener('pointerup', stop);
      viewport.addEventListener('pointercancel', stop);
    }
  });

  viewport.addEventListener('wheel', event => {
    event.preventDefault();
    if (cancelActiveGesture || activeConnection || activeGroupDraw || inlineName) return;
    if (event.ctrlKey || event.metaKey) {
      const bounds = viewport.getBoundingClientRect(), x = event.clientX - bounds.left, y = event.clientY - bounds.top;
      const next = clamp(scale * Math.exp(-event.deltaY * .003), .02, 2);
      panX = x - (x - panX) * next / scale; panY = y - (y - panY) * next / scale; scale = next;
    } else {
      panX -= event.deltaX || (event.shiftKey ? event.deltaY : 0);
      panY -= event.shiftKey ? 0 : event.deltaY;
    }
    applyTransform();
  }, {passive: false});

  root.addEventListener('keydown', async event => {
    const resizeHandle = event.target.closest('[data-group-resize]');
    if (resizeHandle && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault();
      const groupElement = resizeHandle.closest('[data-group-id]');
      const group = groupElement ? groupById(groupElement.dataset.groupId) : null;
      if (!group || mutationBusy) return;
      const step = event.shiftKey ? 40 : 10;
      const members = groupNodes(group.id);
      const memberMinWidth = members.length ? Math.max(...members.map(node => node.x + NODE_WIDTH + 20 - group.x)) : MIN_GROUP_WIDTH;
      const memberMinHeight = members.length ? Math.max(...members.map(node => node.y + NODE_HEIGHT + 20 - group.y)) : MIN_GROUP_HEIGHT;
      if (event.key === 'ArrowLeft') group.width = clamp(group.width - step, Math.max(MIN_GROUP_WIDTH, memberMinWidth), 8000);
      if (event.key === 'ArrowRight') group.width = clamp(group.width + step, Math.max(MIN_GROUP_WIDTH, memberMinWidth), 8000);
      if (event.key === 'ArrowUp') group.height = clamp(group.height - step, Math.max(MIN_GROUP_HEIGHT, memberMinHeight), 8000);
      if (event.key === 'ArrowDown') group.height = clamp(group.height + step, Math.max(MIN_GROUP_HEIGHT, memberMinHeight), 8000);
      render();
      groupLayer.querySelector(`[data-group-id="${group.id}"] [data-group-resize]`)?.focus();
      try {
        await commitDocument(structuredClone(graph), 'Group resized');
        groupLayer.querySelector(`[data-group-id="${group.id}"] [data-group-resize]`)?.focus();
      }
      catch (error) { showStatus(error.message, true); render(); }
      return;
    }
    if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
      if (!event.target.closest('[data-map-viewport]')) return;
      event.preventDefault();
      const bounds = viewport.getBoundingClientRect();
      const clientX = bounds.left + bounds.width / 2;
      const clientY = bounds.top + bounds.height / 2;
      hideFloatingControls();
      contextSurfacePoint = clientToSurface(clientX, clientY);
      positionFloating(mapContextMenu, clientX, clientY);
      return;
    }
    if (!['Enter', ' '].includes(event.key)) return;
    const connectHandle = event.target.closest('[data-connect-handle]');
    if (connectHandle) {
      event.preventDefault();
      event.stopPropagation();
      const sourceElement = connectHandle.closest('[data-node-id]');
      if (sourceElement) armKeyboardConnection(sourceElement.dataset.nodeId);
      return;
    }
    const node = event.target.closest('[data-node-id]');
    const group = event.target.closest('[data-group-id]');
    const edge = event.target.closest('[data-edge-id]');
    const element = node || group || edge;
    if (!element) return;
    event.preventDefault();
    const bounds = element.getBoundingClientRect();
    const clientX = bounds.left + bounds.width / 2;
    const clientY = bounds.top + bounds.height / 2;
    if (node && keyboardConnectionSourceId) {
      const sourceId = keyboardConnectionSourceId;
      if (Number(node.dataset.nodeId) !== sourceId) {
        await quickConnect(sourceId, node.dataset.nodeId, clientX, clientY, true);
      }
    } else if (node) showEntityToolbar('node', node.dataset.nodeId, clientX, clientY);
    else if (group) showEntityToolbar('group', group.dataset.groupId, clientX, clientY);
    else showEdgeToolbar(edge.dataset.edgeId, clientX, clientY);
  });

  document.addEventListener('keydown', async event => {
    if (event.key === 'Escape') {
      activeConnection?.cancel?.();
      cancelGroupDrawing();
      cancelKeyboardConnection();
      activeConnection = null;
      connectionHint.hidden = true;
      hideFloatingControls();
      render();
      return;
    }
    if (!['Delete', 'Backspace'].includes(event.key) || !selection) return;
    if (!root.contains(event.target) || event.target.closest('input,textarea,select,button,a,summary,[contenteditable="true"]') || entityDialog.open || mapDialog.open || shortcutDialog.open || inlineName) return;
    event.preventDefault();
    try { await removeSelection(); }
    catch (error) { showStatus(error.message, true); }
  });

  viewport.classList.add('is-select-mode');
  render();
  applyTransform();
})();
