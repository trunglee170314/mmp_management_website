// Opt-in real-browser regression test. No running app, login or production DB.
// MMP_BROWSER_TESTS=1 PLAYWRIGHT_MODULE=/path/to/playwright node --test this-file
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('System Map browser: details, description search, quick create, clipboard, undo, Escape and shortcuts', {
  skip: process.env.MMP_BROWSER_TESTS !== '1', timeout: 90000,
}, async () => {
  const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
    const errors = [];
    page.on('pageerror', error => { errors.push(error.message); console.error('Map browser error:', error.message); });
    let graph = {
      id: 1, name: 'Browser fixture', description: '', revision: '2026-11-11T00:00:00Z', groups: [], edges: [],
      nodes: [
        {id: 1, name: 'Decoder', description: 'Secret phrase in description only', external_url: 'https://example.test', color: '#3B82F6', x: 80, y: 80, group_id: null, keep_position: false},
        {id: 2, name: 'Renderer', description: 'Second node', external_url: '', color: '#22C55E', x: 450, y: 80, group_id: null, keep_position: false},
      ],
    };
    let serial = 100, revision = 1, returnHtmlOnce = false, returnRejectedOnce = false, returnMalformedOnce = false;
    const requests = [];
    const multipart = data => Object.fromEntries([...data.matchAll(/name="([^"]+)"\r\n\r\n([\s\S]*?)\r\n--/g)].map(match => [match[1], match[2]]));
    const rootPath = path.resolve(__dirname, '../..');
    let html = fs.readFileSync(path.join(rootPath, 'templates/relationship_map.html'), 'utf8');
    html = html.replace(/\{\{\s*graph_data\|json_script:"relationship-map-data"\s*\}\}/, `<script id="relationship-map-data" type="application/json">${JSON.stringify(graph)}</script>`)
      .replace(/\{%[\s\S]*?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, '')
      .replace(/<script src=[\s\S]*?<\/script>/g, '').replace(/<link[^>]*>/g, '')
      .replace('data-api-url=""', 'data-api-url="/system-map/api/"');
    await page.route('http://fixture.test/**', async route => {
      if (!route.request().url().includes('/system-map/api/')) return route.fulfill({contentType: 'text/html', body: html});
      const payload = multipart(route.request().postData() || ''); requests.push(payload);
      assert.equal(payload.base_revision, graph.revision);
      if (returnHtmlOnce) { returnHtmlOnce=false; return route.fulfill({contentType:'text/html',body:'<!doctype html><title>Sign in</title>'}); }
      if (returnRejectedOnce) { returnRejectedOnce=false; return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Rejected test edit'})}); }
      if (returnMalformedOnce) { returnMalformedOnce=false; return route.fulfill({contentType:'application/json',body:JSON.stringify({map:{}})}); }
      if (payload.action === 'save_document') {
        const next = JSON.parse(payload.document);
        const ids = {groups: new Map(), nodes: new Map()};
        for (const key of ['groups', 'nodes']) for (const item of next[key]) {
          const old = item.id; if (item.id < 0) item.id = serial++;
          ids[key].set(old, item.id);
        }
        for (const node of next.nodes) if (node.group_id !== null) node.group_id = ids.groups.get(node.group_id);
        for (const edge of next.edges) { if (edge.id < 0) edge.id = serial++; edge.source_id = ids.nodes.get(edge.source_id); edge.target_id = ids.nodes.get(edge.target_id); }
        graph = {...next, revision: `2026-11-11T00:00:${String(revision++).padStart(2, '0')}Z`};
      } else if (payload.action === 'create_edge') {
        graph.edges.push({id:serial++,source_id:Number(payload.source_id),target_id:Number(payload.target_id),direction:payload.direction,line_style:payload.line_style});
        graph.revision=`2026-11-11T00:01:${String(revision++).padStart(2,'0')}Z`;
      } else if (payload.action === 'update_edge') {
        Object.assign(graph.edges.find(edge=>edge.id===Number(payload.edge_id)),{direction:payload.direction,line_style:payload.line_style});
        graph.revision=`2026-11-11T00:02:${String(revision++).padStart(2,'0')}Z`;
      }
      await route.fulfill({contentType: 'application/json', body: JSON.stringify({updated: true, map: graph})});
    });
    await page.goto('http://fixture.test/');
    await page.addStyleTag({content: ':root{--line:#ddd;--paper:#fff;--ink:#123;--muted:#678;--violet:#006241;--control-bg:#fff} body{margin:0;padding:20px} button{cursor:pointer} .panel{border:1px solid #ddd}' + fs.readFileSync(path.join(__dirname, 'relationship-map.css'), 'utf8')});
    await page.addScriptTag({content: fs.readFileSync(path.join(__dirname, 'relationship-map.js'), 'utf8')});
    assert.equal(await page.locator('[data-node-id]').count(), 2);
    console.log('System Map fixture rendered');

    await page.locator('[data-node-id="1"]').click({position: {x: 25, y: 25}});
    assert.equal(await page.locator('[data-node-detail-name]').textContent(), 'Decoder');
    assert.match(await page.locator('[data-node-detail-description]').textContent(), /Secret phrase/);
    await page.locator('[data-node-search]').fill('phrase description');
    assert.equal(await page.locator('.is-search-match').count(), 1);
    await page.locator('[data-node-search]').press('Enter');
    assert.equal(await page.locator('[data-node-detail-name]').textContent(), 'Decoder');
    await page.locator('[data-node-search]').fill('');
    console.log('System Map details and description search passed');

    await page.locator('[data-map-viewport]').focus();
    await page.keyboard.press('n');
    await page.locator('[aria-label="Node name"]').fill('Quick created');
    await page.keyboard.press('Enter');
    await page.locator('[aria-label="Node name"]').waitFor({state: 'detached'});
    await page.waitForFunction(() => document.querySelectorAll('[data-node-id]').length === 3);
    assert.ok(graph.nodes.some(node => node.name === 'Quick created'));
    await page.keyboard.press('Control+Enter');
    await page.locator('[aria-label="Node name"]').fill('Connected child');
    await page.keyboard.press('Enter');
    await page.locator('[aria-label="Node name"]').waitFor({state: 'detached'});
    await page.waitForFunction(() => document.querySelectorAll('[data-node-id]').length === 4);
    assert.equal(graph.edges.length, 1);
    console.log('System Map quick creation and connected node passed');

    await page.keyboard.press('Control+z');
    await page.waitForFunction(() => document.querySelectorAll('[data-node-id]').length === 3);
    await page.keyboard.press('Control+Shift+z');
    await page.waitForFunction(() => document.querySelectorAll('[data-node-id]').length === 4);
    await page.locator('[data-node-id="1"]').click({position: {x: 20, y: 20}});
    await page.keyboard.press('Control+c'); await page.keyboard.press('Control+v');
    await page.waitForFunction(() => document.querySelectorAll('[data-node-id]').length === 5);
    assert.equal(graph.nodes.filter(node => node.name === 'Decoder').length, 2);
    console.log('System Map Undo/Redo and clipboard passed');

    const before = JSON.stringify(graph), beforeRequests = requests.length;
    const box = await page.locator('[data-node-id="1"]').boundingBox();
    await page.mouse.move(box.x + 25, box.y + 25); await page.mouse.down();
    await page.mouse.move(box.x + 140, box.y + 90, {steps: 5});
    await page.keyboard.press('Control+z'); await page.keyboard.press('n');
    assert.equal(await page.locator('[aria-label="Node name"]').count(), 0, 'Active drag must not create an unconfirmed node');
    await page.keyboard.press('Escape'); await page.mouse.up();
    await page.waitForTimeout(100);
    assert.equal(requests.length, beforeRequests, 'Escape must not save a drag');
    assert.equal(JSON.stringify(graph), before);

    await page.locator('[data-map-viewport]').focus(); await page.keyboard.press('k');
    assert.equal(await page.locator('[data-shortcut-dialog]').evaluate(element => element.open), true);
    await page.locator('[data-shortcut-binding="create"]').focus(); await page.keyboard.press('ArrowLeft');
    assert.match(await page.locator('[data-shortcut-error]').textContent(), /reserved/);
    await page.keyboard.press('u');
    await page.locator('[data-shortcut-form] button[type="submit"]').click();
    await page.locator('[data-map-viewport]').focus(); await page.keyboard.press('u');
    assert.equal(await page.locator('[aria-label="Node name"]').count(), 1);
    await page.keyboard.press('Escape');
    await page.locator('[data-node-id="1"]').dblclick({position: {x: 25, y: 25}});
    assert.equal(await page.locator('[data-entity-dialog]').evaluate(element => element.open), true);
    await page.locator('[data-node-fields] textarea[name="description"]').fill('Updated through node details');
    await page.locator('[data-entity-form] button[type="submit"]').click();
    await page.waitForFunction(() => !document.querySelector('[data-entity-dialog]').open);
    assert.equal(graph.nodes.find(node => node.id === 1).description, 'Updated through node details');
    console.log('System Map double-click modal edit passed');

    // Selection, search and camera must agree for every input method.
    let dialogs=0;page.on('dialog',dialog=>{dialogs++;dialog.dismiss();});
    const camera=()=>page.locator('[data-map-surface]').getAttribute('style');
    const reset=async()=>{await page.locator('[data-map-viewport]').focus();await page.keyboard.press('Escape');await page.waitForTimeout(350);};
    await reset();await page.locator('[data-fit-map]').click();
    await page.locator('[data-node-id="1"]').click({position:{x:25,y:25}});
    const widthWithDetails=(await page.locator('[data-map-viewport]').boundingBox()).width;
    await page.locator('[data-close-node-details]').click();
    assert.equal(await page.locator('[data-node-details]').isVisible(),false);
    assert.ok((await page.locator('[data-map-viewport]').boundingBox()).width>widthWithDetails+200);
    await page.locator('[data-node-id="2"]').click({position:{x:25,y:25},modifiers:['Shift']});
    assert.equal(await page.locator('.relationship-node.is-selected').count(),2);
    await page.waitForTimeout(200);
    assert.deepEqual(await page.locator('.relationship-node.is-selected').evaluateAll(nodes=>nodes.map(node=>getComputedStyle(node).opacity)),['1','1']);
    await page.waitForTimeout(550);
    await page.locator('[data-node-id="2"]').click({position:{x:25,y:25}});
    assert.equal(await page.locator('[data-node-details]').isVisible(),true,'A plain node click must reopen details after multiple selection');
    assert.equal(await page.locator('[data-node-detail-name]').textContent(),'Renderer');
    assert.equal(await page.locator('[data-map-mode]').count(),0,'Mouse mode toggles are unnecessary');

    const panGraph=JSON.stringify(graph),panRequests=requests.length,panCamera=await camera();
    const panNode=await page.locator('[data-node-id="2"]').boundingBox();
    await page.mouse.move(panNode.x+25,panNode.y+25);await page.mouse.down({button:'right'});
    await page.mouse.move(panNode.x+105,panNode.y+70,{steps:6});await page.mouse.up({button:'right'});
    assert.notEqual(await camera(),panCamera,'Right drag on a node must pan the camera');
    assert.equal(JSON.stringify(graph),panGraph,'Right drag must never move nodes in the saved graph');
    assert.equal(requests.length,panRequests,'Camera movement must not save a document');
    assert.equal(await page.locator('[data-entity-context]').isVisible(),false,'Right drag must not open a context menu');
    await page.locator('[data-node-id="2"]').click({button:'right',position:{x:25,y:25}});
    assert.equal(await page.locator('[data-entity-context]').isVisible(),true,'Stationary right click must still open its menu');
    assert.equal(await page.locator('[data-entity-context] [data-map-command="copy"],[data-entity-context] [data-map-command="duplicate"]').count(),0);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(550);
    await page.locator('[data-node-id="1"]').click({position:{x:25,y:25}});
    await page.locator('[data-node-id="2"]').click({position:{x:25,y:25},modifiers:['Shift']});
    const initialCount=graph.nodes.length,beforePaste=await camera();
    await page.locator('[data-map-viewport]').focus();await page.keyboard.press('Control+c');await page.keyboard.press('Control+v');
    await page.waitForFunction(count=>document.querySelectorAll('[data-node-id]').length===count,initialCount+2);
    assert.equal(await camera(),beforePaste,'Paste must not zoom or pan');
    await page.keyboard.press('Control+z');await page.waitForFunction(count=>document.querySelectorAll('[data-node-id]').length===count,initialCount);
    await reset();await page.locator('[data-node-id="1"]').click({position:{x:25,y:25}});
    await page.locator('[data-node-id="2"]').click({position:{x:25,y:25},modifiers:['Shift']});
    await page.waitForTimeout(400);await page.locator('[data-node-id="2"]').click({position:{x:25,y:25}});
    await page.locator('[data-entity-toolbar] [data-map-command="remove"]').click();
    await page.waitForFunction(count=>document.querySelectorAll('[data-node-id]').length===count,initialCount-2);
    assert.equal(dialogs,0,'Deleting nodes must not ask for confirmation');
    assert.ok(!graph.nodes.some(node=>node.id===1||node.id===2),'Floating Delete must delete the full selection');
    assert.equal(await page.locator('[data-map-status] [data-map-command="undo"]').isVisible(),true);
    await page.keyboard.press('Control+z');
    await page.waitForFunction(count=>document.querySelectorAll('[data-node-id]').length===count,initialCount);

    await reset();await page.locator('[data-node-id="2"]').click({position:{x:25,y:25}});
    await page.locator('[data-node-search]').fill('Updated through');await page.waitForTimeout(200);
    assert.equal(await page.locator('.is-search-match').evaluate(node=>getComputedStyle(node).opacity),'1');
    await page.locator('[data-map-viewport]').focus();await page.keyboard.press('Control+a');
    assert.equal(await page.locator('.relationship-node.is-selected').count(),1,'Select all while searching means matching nodes only');
    assert.equal(await page.locator('[data-map-command="nextResult"],[data-map-command="previousResult"]').count(),0);
    await page.locator('[data-node-search]').fill('');await reset();

    // A discarded inline name must never leave the editor permanently locked.
    await page.keyboard.press('u');assert.equal(await page.locator('[aria-label="Node name"]').count(),1);
    await page.locator('[data-map-viewport]').dispatchEvent('click');
    await page.locator('[data-map-viewport]').focus();await page.keyboard.press('u');
    assert.equal(await page.locator('[aria-label="Node name"]').count(),1);
    await page.keyboard.press('Escape');await reset();

    await page.locator('[data-node-id="1"]').click({position:{x:25,y:25}});await page.locator('[data-map-viewport]').focus();await page.keyboard.press('c');
    await page.locator('[data-node-id="2"]').focus();await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('[data-map-status]').textContent==='Connection created');
    const connected=graph.edges.find(edge=>edge.source_id===1&&edge.target_id===2);
    assert.ok(connected,'C, Tab to target, Enter must connect the focused target');
    await reset();await page.locator(`[data-edge-id="${connected.id}"]`).dispatchEvent('click',{clientX:300,clientY:300});
    await page.locator('[data-line-style-toggle]').click();await page.keyboard.press('ArrowDown');
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.lineStyle),'dashed','Edge menu arrows must not navigate canvas nodes');
    await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('[data-map-status]').textContent==='Connection updated');
    assert.equal(graph.edges.find(edge=>edge.id===connected.id).line_style,'dashed');

    // Build a group through the actual UI, then use plain drag to select it.
    await reset();await page.locator('[data-fit-map]').click();await page.locator('[data-map-viewport]').focus();await page.keyboard.press('b');
    const viewportBox=await page.locator('[data-map-viewport]').boundingBox();
    await page.mouse.click(viewportBox.x+viewportBox.width-60,viewportBox.y+80);
    await page.locator('[data-group-fields] [name="group_name"]').fill('Browser group');
    await page.locator('[data-entity-form] button[type="submit"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('[data-group-id]').length===1);
    await reset();await page.locator('[data-fit-map]').click();
    const beforeCollapse=requests.length;
    await page.locator('[data-group-id] .relationship-group-header').dblclick({position:{x:30,y:20}});
    await page.waitForFunction(()=>document.querySelector('[data-group-id]').classList.contains('is-collapsed'));
    assert.equal(graph.groups[0].is_collapsed,true);
    assert.equal(requests.slice(beforeCollapse).filter(request=>request.action==='save_document').length,1,'Double-click must toggle the group only once');
    await reset();await page.locator('[data-group-id] .relationship-group-header').dblclick({position:{x:30,y:20}});
    await page.waitForFunction(()=>!document.querySelector('[data-group-id]').classList.contains('is-collapsed'));
    await reset();await page.locator('[data-fit-map]').click();
    const groupBox=await page.locator('[data-group-id]').boundingBox();
    await page.mouse.move(groupBox.x-20,groupBox.y-20);await page.mouse.down();await page.mouse.move(groupBox.x+groupBox.width+20,groupBox.y+groupBox.height+20,{steps:8});await page.mouse.up();
    assert.equal(await page.locator('.relationship-group.is-selected').count(),1,'Default selection must include fully enclosed groups');
    const groupCount=graph.groups.length;await page.locator('[data-map-viewport]').focus();await page.keyboard.press('Control+c');
    const groupCamera=await camera();await page.keyboard.press('Control+v');await page.waitForFunction(count=>document.querySelectorAll('[data-group-id]').length===count,groupCount+1);
    assert.equal(await camera(),groupCamera,'Copying an empty group must not move the camera');
    await reset();await page.locator('[data-fit-map]').click();

    // A group and its explicitly selected member move once, not twice.
    const originalGroupId=graph.groups[0].id;
    await page.locator('[data-node-id="1"]').dblclick({position:{x:25,y:25}});
    await page.locator('[name="node_group_id"]').selectOption(String(originalGroupId));
    await page.locator('[data-entity-form] button[type="submit"]').click();await page.waitForFunction(()=>!document.querySelector('[data-entity-dialog]').open);
    await reset();await page.locator('[data-fit-map]').click();
    const memberGroupBox=await page.locator(`[data-group-id="${originalGroupId}"]`).boundingBox();
    await page.mouse.move(memberGroupBox.x-20,memberGroupBox.y-20);await page.mouse.down();await page.mouse.move(memberGroupBox.x+memberGroupBox.width+20,memberGroupBox.y+memberGroupBox.height+20,{steps:8});await page.mouse.up();
    assert.equal(await page.locator(`[data-group-id="${originalGroupId}"].is-selected`).count(),1);
    assert.equal(await page.locator('[data-node-id="1"].is-selected').count(),1);
    const groupBeforeMove=structuredClone(graph.groups.find(group=>group.id===originalGroupId)),nodeBeforeMove=structuredClone(graph.nodes.find(node=>node.id===1));
    const headerBox=await page.locator(`[data-group-id="${originalGroupId}"] .relationship-group-header`).boundingBox();
    await page.mouse.move(headerBox.x+30,headerBox.y+18);await page.mouse.down();await page.mouse.move(headerBox.x+78,headerBox.y+42,{steps:6});await page.mouse.up();
    await page.waitForFunction(()=>document.querySelector('[data-map-status]').textContent==='Move selected nodes');
    const movedGroup=graph.groups.find(group=>group.id===originalGroupId),movedNode=graph.nodes.find(node=>node.id===1);
    assert.ok(Math.abs((movedGroup.x-groupBeforeMove.x)-(movedNode.x-nodeBeforeMove.x))<0.01);
    assert.ok(Math.abs((movedGroup.y-groupBeforeMove.y)-(movedNode.y-nodeBeforeMove.y))<0.01);

    // Node-only paste outside its source group must not stretch that group.
    await reset();await page.locator('[data-fit-map]').click();await page.locator('[data-node-id="1"]').click({position:{x:25,y:25}});
    await page.locator('[data-map-viewport]').focus();await page.keyboard.press('Control+c');
    const groupBeforePaste=structuredClone(graph.groups.find(group=>group.id===originalGroupId));
    const beforeOutsidePaste=new Set(graph.nodes.map(node=>node.id)),viewportForPaste=await page.locator('[data-map-viewport]').boundingBox();
    await page.locator('[data-map-viewport]').dispatchEvent('contextmenu',{clientX:viewportForPaste.x+12,clientY:viewportForPaste.y+12});
    await page.locator('[data-context-paste]').click();
    await page.waitForFunction(count=>document.querySelectorAll('[data-node-id]').length===count,beforeOutsidePaste.size+1);
    assert.equal(graph.nodes.find(node=>!beforeOutsidePaste.has(node.id)).group_id,null);
    assert.deepEqual(graph.groups.find(group=>group.id===originalGroupId),groupBeforePaste);
    await reset();await page.locator('[data-fit-map]').click();

    // A rejected form edit must not leave Undo/Redo visually disabled.
    await page.locator('[data-node-id="1"]').dblclick({position:{x:25,y:25}});
    await page.locator('[data-node-fields] textarea[name="description"]').fill('Rejected draft');returnRejectedOnce=true;
    await page.locator('[data-entity-form] button[type="submit"]').click();
    await page.waitForFunction(()=>document.querySelector('[data-entity-form] [data-dialog-error]').textContent==='Rejected test edit');
    assert.equal(await page.locator('[data-map-command="undo"]').isEnabled(),true);
    await page.locator('[data-entity-form] [data-dialog-close]').first().click();
    await reset();

    // HTML login/error pages must never be interpreted as a successful save.
    await page.locator('[data-node-id="1"]').click({position:{x:25,y:25}});await page.locator('[data-map-viewport]').focus();await page.keyboard.press('F2');
    await page.locator('[aria-label="Node name"]').fill('Must not be saved');returnHtmlOnce=true;await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('[data-map-status]').textContent.includes('Unable to confirm'));
    assert.equal(graph.nodes.find(node=>node.id===1).name,'Decoder');
    await page.locator('[data-map-viewport]').focus();await page.keyboard.press('F2');await page.locator('[aria-label="Node name"]').fill('Malformed must not save');returnMalformedOnce=true;await page.keyboard.press('Enter');
    await page.waitForFunction(()=>document.querySelector('[data-map-status]').textContent.includes('Invalid saved map'));
    assert.equal(graph.nodes.find(node=>node.id===1).name,'Decoder');
    assert.equal(dialogs,0);
    console.log('System Map regression checks: selection, deletion, clipboard camera, group area selection, keyboard connect and failed-save rollback passed');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
