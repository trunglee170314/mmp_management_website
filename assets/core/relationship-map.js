(() => {
  const root = document.querySelector('[data-relationship-root]');
  if (!root) return;

  const dataElement = document.getElementById('relationship-map-data');
  let graph = dataElement ? JSON.parse(dataElement.textContent) : null;
  let lastSavedGraph = graph ? structuredClone(graph) : null;
  let mutationChain = Promise.resolve();
  let mutationBusy = 0;
  const csrf = root.querySelector('[name="csrfmiddlewaretoken"]')?.value || '';
  const mapDialog = root.querySelector('[data-map-dialog]');
  const mapForm = root.querySelector('[data-map-form]');

  const post = formData => {
    const execute = async () => {
      mutationBusy += 1;
      try {
        if (graph?.revision && formData.get('action') !== 'create_map') {
          formData.set('base_revision', graph.revision);
        }
        const response = await fetch(root.dataset.apiUrl, {
          method: 'POST',
          headers: {'X-CSRFToken': csrf, 'Accept': 'application/json'},
          body: formData,
        });
        const raw = await response.text();
        let data = {};
        try { data = raw ? JSON.parse(raw) : {}; } catch (_error) { data = {}; }
        if (data.map) {
          graph = data.map;
          lastSavedGraph = structuredClone(data.map);
        }
        if (!response.ok) {
          const error = new Error(data.error || `Unable to update System Map (${response.status}). Refresh and try again.`);
          error.isConflict = response.status === 409;
          throw error;
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

  const escapeHtml = value => String(value ?? '').replace(
    /[&<>'"]/g,
    char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[char]),
  );
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const nodeById = id => graph.nodes.find(node => node.id === Number(id));
  const edgeById = id => graph.edges.find(edge => edge.id === Number(id));
  const groupById = id => graph.groups.find(group => group.id === Number(id));
  const groupNodes = groupId => graph.nodes.filter(node => node.group_id === Number(groupId));
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
    statusElement.classList.toggle('is-error', isError);
    statusElement.hidden = false;
    statusTimer = window.setTimeout(() => { statusElement.hidden = true; }, isError ? 6500 : 2200);
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
    [mapContextMenu, edgeContextMenu, entityToolbar, edgeToolbar].forEach(element => {
      if (element) element.hidden = true;
    });
    if (!keepSelection) selection = null;
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
      x: clamp((clientX - bounds.left - panX) / scale, 0, SURFACE_WIDTH),
      y: clamp((clientY - bounds.top - panY) / scale, 0, SURFACE_HEIGHT),
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

  const isNodeCollapsed = node => Boolean(node.group_id && groupById(node.group_id)?.is_collapsed);

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
    const siblings = graph.edges.filter(candidate => (
      [candidate.source_id, candidate.target_id].sort((a, b) => a - b).join(':') === pair
    ));
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
        x: clamp(-panX / scale, 0, SURFACE_WIDTH),
        y: clamp(-panY / scale, 0, SURFACE_HEIGHT),
        width: Math.min(SURFACE_WIDTH, viewport.clientWidth / scale),
        height: Math.min(SURFACE_HEIGHT, viewport.clientHeight / scale),
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
    const activeId = selection?.kind === 'node' ? selection.id : nodeId;
    surface.classList.toggle('has-focus', Boolean(activeId));
    const related = activeId ? directRelations(activeId) : {nodes: new Set(), edges: new Set()};
    nodeLayer.querySelectorAll('[data-node-id]').forEach(element => {
      const id = Number(element.dataset.nodeId);
      element.classList.toggle('is-focused', id === activeId);
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
    const visibleNodeIds = new Set();
    nodeLayer.querySelectorAll('[data-node-id]').forEach(element => {
      const node = nodeById(element.dataset.nodeId);
      const haystack = `${node.name} ${node.description || ''} ${groupById(node.group_id)?.name || ''}`.toLocaleLowerCase();
      const filtered = query && !haystack.includes(query);
      element.classList.toggle('is-filtered', Boolean(filtered));
      if (!filtered && !element.hidden) visibleNodeIds.add(node.id);
    });
    edgeLayer.querySelectorAll('[data-edge-id]').forEach(element => {
      const edge = edgeById(element.dataset.edgeId);
      const filtered = hasFilter && (!visibleNodeIds.has(edge.source_id) || !visibleNodeIds.has(edge.target_id));
      element.querySelector('.relationship-edge')?.classList.toggle('is-filtered', filtered);
    });
  };

  const render = () => {
    groupLayer.replaceChildren();
    graph.groups.forEach(group => {
      const nodeCount = groupNodes(group.id).length;
      const element = document.createElement('section');
      element.className = `relationship-group${group.is_collapsed ? ' is-collapsed' : ''}${selection?.kind === 'group' && selection.id === group.id ? ' is-selected' : ''}`;
      element.tabIndex = 0;
      element.dataset.groupId = group.id;
      element.style.cssText = `left:${group.x}px;top:${group.y}px;width:${group.width}px;height:${group.height}px;--group-color:${group.color}`;
      element.innerHTML = `<div class="relationship-group-header">
        <span class="relationship-group-title">${escapeHtml(group.name)}</span>
        <span class="relationship-group-count">${nodeCount} Node${nodeCount === 1 ? '' : 's'}</span>
      </div>${!group.is_collapsed ? '<button type="button" class="relationship-group-resize" data-group-resize aria-label="Resize Group with arrow keys"></button>' : ''}`;
      groupLayer.append(element);
    });

    nodeLayer.replaceChildren();
    graph.nodes.forEach(node => {
      const element = document.createElement('article');
      element.className = `relationship-node${selection?.kind === 'node' && selection.id === node.id ? ' is-selected' : ''}`;
      element.tabIndex = 0;
      element.dataset.nodeId = node.id;
      element.hidden = isNodeCollapsed(node);
      element.style.cssText = `left:${node.x}px;top:${node.y}px;--node-color:${node.color}`;
      element.innerHTML = `<strong>${escapeHtml(node.name)}</strong><button type="button" class="node-connect-handle" data-connect-handle title="Drag to connect" aria-label="Connect from ${escapeHtml(node.name)}"></button>`;
      nodeLayer.append(element);
    });

    renderEdges();
    renderMinimap();
    root.querySelector('[data-map-empty]').hidden = graph.nodes.length !== 0;
    refreshGroupOptions();
    applyFilters();
    applyFocus(hoveredNodeId);
  };

  const showEntityToolbar = (kind, id, clientX, clientY, focusToolbar = false) => {
    const item = kind === 'node' ? nodeById(id) : groupById(id);
    if (!item) return;
    cancelGroupDrawing();
    hideFloatingControls({keepSelection: true});
    selection = {kind, id: Number(id)};
    render();
    const openLink = kind === 'node' && item.external_url
      ? `<a href="${escapeHtml(item.external_url)}" target="_blank" rel="noopener" title="Open link" aria-label="Open ${escapeHtml(item.name)} link">↗</a>`
      : '';
    entityToolbar.innerHTML = `${openLink}<button type="button" data-floating-edit="${kind}" data-id="${item.id}">Edit</button>
      <button type="button" class="text-danger" data-floating-delete="${kind}" data-id="${item.id}">Delete</button>`;
    positionFloating(entityToolbar, clientX, clientY);
    if (focusToolbar) entityToolbar.querySelector('a, button')?.focus();
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
      x: clamp(centerX - width / 2 + offset, 10, SURFACE_WIDTH - width - 10),
      y: clamp(centerY - height / 2 + offset, 10, SURFACE_HEIGHT - height - 10),
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
      refreshGroupOptions(item?.group_id || '');
      fields.querySelector('[name="color"]').value = item?.color || '#ffffff';
    } else if (kind === 'group') {
      entityForm.elements.action.value = item ? 'update_group' : 'create_group';
      entityForm.elements.group_id.value = item?.id || '';
      const fields = entityForm.querySelector('[data-group-fields]');
      fields.hidden = false;
      fields.querySelector('[name="group_name"]').value = item?.name || '';
      fields.querySelector('[name="group_color"]').value = item?.color || '#eaf4ee';
      fields.querySelector('[name="group_width"]').value = Math.round(item?.width ?? position.width ?? 360);
      fields.querySelector('[name="group_height"]').value = Math.round(item?.height ?? position.height ?? 260);
      fields.querySelector('[name="group_collapsed"]').value = String(Boolean(item?.is_collapsed));
    }

    root.querySelector('[data-entity-dialog-title]').textContent = `${item ? 'Edit' : 'New'} ${kind[0].toUpperCase()}${kind.slice(1)}`;
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
    if (!item || !window.confirm(`Delete this ${kind}?${extra}`)) return;
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
        return openEntityDialog('node', null, {
          x: clamp(point.x - NODE_WIDTH / 2, 0, SURFACE_WIDTH - NODE_WIDTH),
          y: clamp(point.y - NODE_HEIGHT / 2, 0, SURFACE_HEIGHT - NODE_HEIGHT),
        });
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

  viewport.addEventListener('contextmenu', event => {
    event.preventDefault();
    cancelGroupDrawing();
    const edgeElement = event.target.closest('[data-edge-id]');
    if (edgeElement) {
      hideFloatingControls({keepSelection: true});
      selection = {kind: 'edge', id: Number(edgeElement.dataset.edgeId)};
      render();
      positionFloating(edgeContextMenu, event.clientX, event.clientY);
      return;
    }
    const nodeElement = event.target.closest('[data-node-id]');
    if (nodeElement) return showEntityToolbar('node', nodeElement.dataset.nodeId, event.clientX, event.clientY);
    const groupElement = event.target.closest('[data-group-id]');
    if (groupElement) return showEntityToolbar('group', groupElement.dataset.groupId, event.clientX, event.clientY);
    hideFloatingControls();
    contextSurfacePoint = clientToSurface(event.clientX, event.clientY);
    positionFloating(mapContextMenu, event.clientX, event.clientY);
  });

  groupLayer.addEventListener('dblclick', async event => {
    const header = event.target.closest('.relationship-group-header');
    const groupElement = event.target.closest('[data-group-id]');
    if (!header || !groupElement || mutationBusy) return;
    event.preventDefault();
    event.stopPropagation();
    const group = groupById(groupElement.dataset.groupId);
    hideFloatingControls({keepSelection: true});
    try {
      const result = await postAction({
        action: 'update_group', map_id: graph.id, group_id: group.id,
        name: group.name, color: group.color, x: group.x, y: group.y,
        width: group.width, height: group.height, is_collapsed: String(!group.is_collapsed),
      });
      updateGraph(result, group.is_collapsed ? 'Group expanded' : 'Group collapsed');
    } catch (error) { showStatus(error.message, true); }
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
      const result = await post(formData);
      updateGraph(result);
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
  root.querySelector('[data-node-search]').addEventListener('input', applyFilters);
  root.querySelector('[data-zoom-in]').addEventListener('click', () => { scale = Math.min(2, scale + .1); applyTransform(); });
  root.querySelector('[data-zoom-out]').addEventListener('click', () => { scale = Math.max(.35, scale - .1); applyTransform(); });

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
    const bounds = visibleBounds();
    if (!bounds) return;
    const width = Math.max(1, bounds.right - bounds.left);
    const height = Math.max(1, bounds.bottom - bounds.top);
    scale = Math.max(.35, Math.min(1.25, (viewport.clientWidth - 80) / width, (viewport.clientHeight - 80) / height));
    panX = (viewport.clientWidth - width * scale) / 2 - bounds.left * scale;
    panY = (viewport.clientHeight - height * scale) / 2 - bounds.top * scale;
    applyTransform();
  });

  const localGroupLayout = group => {
    const members = groupNodes(group.id);
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
        localGroupLayout(group);
        if (x + group.width > SURFACE_WIDTH - 40) { x = 60; y += rowHeight + 45; rowHeight = 0; }
        group.x = x;
        group.y = y;
        localGroupLayout(group);
        x += group.width + 45;
        rowHeight = Math.max(rowHeight, group.height);
      });
      const ungrouped = graph.nodes.filter(node => !node.group_id);
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
        localGroupLayout(group);
        const angle = graph.groups.length ? index / graph.groups.length * Math.PI * 2 : 0;
        group.x = clamp(centerX + Math.cos(angle) * radius - group.width / 2, 20, SURFACE_WIDTH - group.width - 20);
        group.y = clamp(centerY + Math.sin(angle) * radius - group.height / 2, 20, SURFACE_HEIGHT - group.height - 20);
        localGroupLayout(group);
      });
      const ungrouped = graph.nodes.filter(node => !node.group_id);
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
    if (mutationBusy) return showStatus('Wait for the current change to finish.', true);
    applyAutomaticLayout(layout);
    const bounds = visibleBounds(true);
    if (bounds && (bounds.left < 0 || bounds.top < 0 || bounds.right > SURFACE_WIDTH || bounds.bottom > SURFACE_HEIGHT)) {
      graph = structuredClone(lastSavedGraph);
      render();
      showStatus('This Map is too large for that automatic layout. Arrange it manually or use smaller Groups.', true);
      return;
    }
    render();
    try {
      const result = await postAction({
        action: 'layout_entities', map_id: graph.id,
        nodes: JSON.stringify(graph.nodes.map(({id, x, y, group_id}) => ({id, x, y, group_id}))),
        groups: JSON.stringify(graph.groups.map(({id, x, y, width, height, is_collapsed}) => ({id, x, y, width, height, is_collapsed}))),
      });
      updateGraph(result, 'Layout saved');
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

  viewport.addEventListener('pointerdown', event => {
    const nodeElement = event.target.closest('[data-node-id]');
    const groupElement = event.target.closest('[data-group-id]');
    const resizeHandle = event.target.closest('[data-group-resize]');
    const connectHandle = event.target.closest('[data-connect-handle]');

    // Right-click is reserved for context menus and must never start a drag,
    // resize, connection, or canvas pan operation.
    if (event.button !== 0) return;

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
          x: clamp(rawX, 0, SURFACE_WIDTH - width),
          y: clamp(rawY, 0, SURFACE_HEIGHT - height),
          width: Math.min(width, SURFACE_WIDTH),
          height: Math.min(height, SURFACE_HEIGHT),
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
    if (!event.ctrlKey) return;
    event.preventDefault();
    scale = Math.max(.35, Math.min(2, scale + (event.deltaY < 0 ? .08 : -.08)));
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
      if (event.key === 'ArrowLeft') group.width = clamp(group.width - step, Math.max(MIN_GROUP_WIDTH, memberMinWidth), SURFACE_WIDTH - group.x);
      if (event.key === 'ArrowRight') group.width = clamp(group.width + step, Math.max(MIN_GROUP_WIDTH, memberMinWidth), SURFACE_WIDTH - group.x);
      if (event.key === 'ArrowUp') group.height = clamp(group.height - step, Math.max(MIN_GROUP_HEIGHT, memberMinHeight), SURFACE_HEIGHT - group.y);
      if (event.key === 'ArrowDown') group.height = clamp(group.height + step, Math.max(MIN_GROUP_HEIGHT, memberMinHeight), SURFACE_HEIGHT - group.y);
      render();
      groupLayer.querySelector(`[data-group-id="${group.id}"] [data-group-resize]`)?.focus();
      try {
        await saveLayout({groups: [group]}, 'Group resized');
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
    if (event.target.matches('input, textarea, select, [contenteditable="true"]') || entityDialog.open || mapDialog.open) return;
    event.preventDefault();
    try { await deleteEntity(selection.kind, selection.id); }
    catch (error) { showStatus(error.message, true); }
  });

  render();
  applyTransform();
})();
