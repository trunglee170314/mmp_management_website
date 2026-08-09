This guide focuses on behavior that is easy to miss. Labels and common controls are intentionally not repeated here.

## Accounts and passwords

- Internal-workspace passwords have no minimum-length, complexity, common-password or personal-information restrictions. Passwords must still be non-empty, and confirmation fields must match. Short/simple passwords are easier to guess; this relaxed policy is intended for the internal workspace.
- Self-service password changes still require the current password. Forgotten-password requests still need an Admin to reset them. Passwords remain hashed; existing passwords, account approval and permissions are unchanged.

## Task and Action Item lifecycle

- A Task and its Action Items have independent statuses. A Task may be marked Done while one or more Action Items remain open.
- Changing a Task assignee changes who currently owns that Task. Dashboard totals, including completed totals, use the current assignee.
- Task status and assignee may be changed independently. Reopening is not required before changing the assignee.
- Action Items can be added, edited, reassigned, completed, reopened or cancelled regardless of the Task status.
- Completed Action Items remain readable and do not use strikethrough text.

## Meeting Minutes

- A new Meeting starts with every current `In Progress` Task. If another Task later becomes `In Progress`, it is appended to every active Draft without changing the existing order. Leaving `In Progress` does not remove it.
- Draft meetings are live workspaces. Progress, Task details and published Action Items stay synchronized with Task Management. Action Items added from Task Management after the Meeting was created also appear there.
- Earlier Action Items can be edited inline using the same fields as new Action Items.
- Finalize creates a read-only meeting snapshot. Updates made later in Task Management do not rewrite that snapshot.
- Reopen returns the same meeting to Draft; it does not rebuild or expand the historical Task list.
- Quick Create needs only a date. The creator is the default Host. Writer Assignment uses the first eligible active rotation; when no rotation can supply a writer, it safely falls back to Manual with the creator as Minute Writer. Quick Create opens an existing Draft instead of duplicating the same date.
- Use `Arrow Up` and `Arrow Down` to move between Task cards without marking either Task reviewed. Shortcuts are disabled while typing or using a form control.
- `Reviewed`, `No Update` and `Skipped` are explicit review outcomes. The system does not auto-skip a Task because it has no Action Items.

## Timeline and milestones

- Open **Timeline** from the sidebar. Scrolling to either edge extends the calendar and retains all dates already opened in this view; it never shrinks back to a moving date window. Only nearby day cells are rendered. **Today** returns to the current date without discarding opened years; zoom changes the time scale, not the Task text size.
- Click empty calendar space for **Task / Milestone / Group / Holiday** (Group/Holiday management remains Admin-only). Drag empty space to select a Task date range. Group edit/delete is on its header; Holidays can be opened from the day header or the list below.
- Creating a Task or clicking its bar opens the same Add/Edit form as Task Management, including searchable Scope, Parent, Related Task and Device pickers. Save stays on the Timeline and preserves your view.
- Drag a Task bar horizontally to shift dates, vertically onto a Group (including a collapsed header or Ungrouped) to change its Group, or diagonally to do both. Handles change dates only. Dropping onto the task-tree gutter changes Group without shifting dates. `Ctrl+Z` restores dates and Group only if nobody else has changed that Task; there is no Saved/Undo toast. Errors and conflicts remain visible. Scheduled rows load as needed; Unscheduled Tasks have a separate collapsible list with Load more.
- Toggle the task tree beside Today: open shows Group → Task → Subtask branches and one Task per row; closed packs non-overlapping bundles in the same saved Task order. Each Task first tries the row of its nearest preceding Task, then earlier rows if occupied. Bars, milestone badges and leaders all count as occupied space, even outside the visible date slice. Dates never automatically sort this order.
- Drag a Task vertically on the calendar to change sibling order without changing dates; when compact, the destination row must not overlap its bundle. In the open tree, drag its grip or bar into the middle of another Task's name to make it a subtask. Drop near the top/bottom edge to place before/after that Task and adopt its parent, or onto the Group heading to remove the parent. A highlight/line previews the drop. A collapsed destination parent opens after a successful drop. Moving a parent keeps its subtree attached; self/descendant cycles and duplicate Related/Parent links are rejected. Parent changes stay within the same Group and never change dates.
- Both order and parent changes persist across reload/display modes, record Task History, and support `Ctrl+Z` unless someone else changed the relevant Task/tree. Undo restores parent/order only, not other fields. `Alt+↑/↓` on a tree grip reorders siblings without changing parent. Horizontal dates and cross-Group calendar dragging remain separate actions.
- Tree branches depict actual parent/subtask links. Parents are bold with expand/collapse arrows; collapsing one hides descendant rows and flags, including milestones of unscheduled descendants. Missing, archived, cross-Group or filtered parents do not hide otherwise-visible Tasks. Tree state is remembered; toggling preserves the viewed dates and zoom.
- Schedule changes appear in Task History and live Draft Meetings; Finalized Meeting snapshots are unchanged. Old Task edit forms also reject stale saves instead of silently overwriting timeline changes.
- Milestones are small flag/star overlays, never separate blank rows. Choose whether one belongs to the Timeline, a Group or a Task. A Task milestone follows its Task's Group, but its date stays independent when the Task is moved. A milestone for an unscheduled Task remains visible on the header/list.
- Flags on the same date cluster only when they belong to the same Task, Group or Timeline. Click a numbered flag for its list and Edit/View controls. Hover identifies the Group/Task path; stars mean Important, not a different owner type. The Milestone Task selector has searchable choices inside its dropdown.
- **Shared** is the default. **Private** milestones are visible/editable only to their creator, including when another viewer is an Admin or superuser. Their content, dates, counts, layout and history are excluded for others. This is application access control, not encryption against the server/database operator or backup administrator.
- Creators manage their milestones; Admins can edit Shared milestones, but only the creator can change visibility. Previously-private history remains owner-only after sharing, and deleted private audit records remain private even if their creator account is deleted.
- Below the chart, **Milestones | Holidays** share the available width (stacked on small screens). Milestones are grouped in collapsible Group → Task sections, with separate Timeline and Ungrouped sections. Filters offer Next 7/30 days, Today, Past and All, plus Important only. Click a name to jump to its date or Edit/View to inspect it. A past date does not imply completion.
- Milestones use Auto or eight preset colors. Timeline Groups/Holidays and System Map Nodes/Groups use the same eight-color palette without Auto. Old stored colors are preserved until explicitly changed.

## Dashboard metrics

- Filters use the selected year and member. Member totals are attributed to the Task's current assignee, including completed Tasks.
- Active Tasks are Tasks that are not Done. Completed charts use Tasks whose current status is Done.

## Appearance

- Appearance is saved per user. Choose **Automatic** or one of the four color swatches from the account menu.
- Automatic changes tone at 06:00, 10:00, 14:00 and 18:00, ending the timed cycle at 21:00. Outside 06:00–21:00 it follows the operating system's light/dark preference.

## Devices

- Use **+ > Assign** in the Device list, or **Assigned Users** in Add/Edit Device,
  to manage manual user assignments. Changes appear in Device history. Users
  assigned through Tasks are shown separately; releasing a manual assignment does
  not release an assignment from a linked Task.
- Admins can choose **Archive** to keep a Device and its history available for restore. Restore does not reactivate previously released Task or user assignments.
- **Delete** permanently removes any Device, including its components, Task links, assignments and Device audit history, and releases its barcode. This cannot be undone. Archived Devices can also be deleted permanently.
- If a barcode already belongs to an active or archived Device, the form identifies that Device instead of showing a generic duplicate error.
- A Task can be linked to multiple Devices. Device visibility and Task status are separate concerns.
- One Device can contain any number of named component links, such as `SoM` and `Carrier`. Components belong to the Device; Tasks continue to reference the Device as a whole.
- Device Description and component changes are recorded in Device Change History. Existing single links are preserved as a `Main` component during upgrade.

## System Map

- Maps are independent free-form canvases. Nodes do not have to follow columns or a fixed hierarchy.
- Groups are the only classification layer; the former Node Type is intentionally not exposed. Assigning a Node to a Group automatically places it in the next free slot and expands the Group when needed.
- Right-click empty canvas space to add a Node or Group at that position. When adding a Group, drag its initial rectangle before entering its name and color. Keyboard users can focus the canvas and press `Shift+F10` or the Context Menu key.
- The compact toolbar contains Map selection, Search and Undo/Redo. The **⋯** menu holds Map details, History, Trash, Shortcuts and Grid/Circle layout; Zoom/Fit is at the bottom of the canvas.
- Left-drag blank canvas space to select Nodes and fully enclosed Groups; left-drag an object to move the selection. `Shift` extends/toggles selection; `Ctrl+A` selects visible objects (only matching Nodes while searching). Hold the right mouse button and drag to pan the camera, even over a Node. A right-click without dragging opens the context menu. `Space` + left-drag also pans.
- Click a Node or Group for Edit/Delete. A Node opens its details sidebar; **×** closes the sidebar and releases the canvas width. Double-click a Node to edit, or a Group header to collapse/expand. Group resize handles appear only while selected; arrow keys resize from a focused handle (`Shift` for larger steps).
- Connections are created by dragging the red handle from the source Node to the target Node. With a keyboard, activate the handle and then activate the target Node. Click a connection, then use the line menu for solid/dashed or the arrow menu for no arrow, one-way and two-way. A one-way arrow follows the original drag direction.
- Hover a Node to highlight its direct relatives and their connections in neon red. Click the Node to lock that focus; click empty space or press `Escape` to clear it.
- Delete removes all explicitly selected Nodes without confirmation, including their connections. Deleting a Group preserves its Nodes as ungrouped unless those Nodes are also explicitly selected. **Ctrl+Z** restores the edit, including removed connections. Undo/Redo holds the last 20 local edits and resets on reload or an editor conflict; server History remains available separately. Moving a selected Group and its selected members moves each Node once.
- Search matches Node names and Descriptions without changing saved data. Matching and selected Nodes remain bright. `Enter`/`Shift+Enter` or `F3`/`Alt+F3` visits results; Fit results fits all matches.
- Right-click Nodes/Groups for Edit/Delete, Keep position and selection arrangements. Copy/Duplicate use keyboard shortcuts; right-click blank space for **Paste** at that position. Pasting never changes zoom or pan. A clipboard source deleted in the same Map can be recovered from available server History; purged source history cannot be bypassed. Inline names save with `Enter`; `Escape` or leaving the field cancels an unconfirmed name. Escape cancels an active drag without saving it.
- Dragging offers alignment guides; Auto layout includes branch-right, alignment/distribution, Grid and Circle. **Keep position** excludes pinned Nodes from automatic layout, but manual dragging remains possible.
- Every System Map change creates a restore point. Open **History** to review who changed the Map and when; Admins can restore an earlier state.
- Deleting a Map moves it to **Trash** for 30 days. Admins can restore it or permanently delete it sooner.

### System Map keyboard shortcuts

Shortcuts apply to the canvas, not while editing text. Press **K** to customize them; reserved browser/native text-editing combinations cannot be overridden.

| Keys (Windows) | Action |
| --- | --- |
| N / Ctrl+Enter / Shift+Enter | New Node / connected Node / nearby Node |
| E / F2 / Delete / Escape | Edit / inline rename / delete / cancel |
| R / Ctrl+A / Shift+click | Area selection / select visible objects / extend selection |
| Ctrl+C / Ctrl+V / Shift+D | Copy / Paste / Duplicate cluster |
| Ctrl+Z / Ctrl+Shift+Z | Undo / Redo |
| B / Shift+B | Draw Group / collapse or expand Group |
| C then Enter / L / A | Connect / change line style / cycle arrow direction |
| Space+drag / Alt+arrows | Pan / move selected Nodes |
| + / - / 0 / F / slash | Zoom in / out / 100% / Fit / search |
| J / Shift+J / Shift+P | Layout menu / branch-right / Keep position |

## Permissions

- Every active role can edit Tasks and can add, edit, reassign, complete, reopen or cancel Action Items.
- Draft Meeting progress and inline Task/Action Item updates are available to active users. Meeting setup and lifecycle controls remain role-specific.
- Administrative account and workspace maintenance remains in the Admin area.
- Finalized Meeting content is read-only for all roles until the Meeting is reopened.

## FAQ

- **Why is a Task missing from a Draft Meeting?** It has not entered `In Progress`, or the Meeting is a reopened historical Draft.
- **Why is a Task still present after its status changed?** Existing Meetings keep their original Task list.
- **Why did a completed total move to another member?** Metrics follow the Task's current assignee, not the assignee at completion time.
- **Why can I not edit a Meeting?** It is Finalized, or your account is inactive.
