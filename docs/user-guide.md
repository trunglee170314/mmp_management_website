This guide focuses on behavior that is easy to miss. Labels and common controls are intentionally not repeated here.

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

## Dashboard metrics

- Filters use the selected year and member. Member totals are attributed to the Task's current assignee, including completed Tasks.
- Active Tasks are Tasks that are not Done. Completed charts use Tasks whose current status is Done.

## Appearance

- Appearance is saved per user. Choose **Automatic** or one of the four color swatches from the account menu.
- Automatic changes tone at 06:00, 10:00, 14:00 and 18:00, ending the timed cycle at 21:00. Outside 06:00–21:00 it follows the operating system's light/dark preference.

## Boards

- Admins can choose **Archive** to keep a Board and its history available for restore. Restore does not reactivate previously released Task or user assignments.
- **Delete** permanently removes any Board, including its components, Task links, assignments and Board audit history, and releases its barcode. This cannot be undone. Archived Boards can also be deleted permanently.
- If a barcode already belongs to an active or archived Board, the form identifies that Board instead of showing a generic duplicate error.
- A Task can be linked to multiple Boards. Board visibility and Task status are separate concerns.
- One Board can contain any number of named component links, such as `SoM` and `Carrier`. Components belong to the Board; Tasks continue to reference the Board as a whole.
- Board Description and component changes are recorded in Board Change History. Existing single links are preserved as a `Main` component during upgrade.

## System Relationship Map

- Maps are independent free-form canvases. Nodes do not have to follow columns or a fixed hierarchy.
- Groups are the only classification layer; the former Node Type is intentionally not exposed. Assigning a Node to a Group automatically places it in the next free slot and expands the Group when needed.
- Right-click empty canvas space to add a Node or Group at that position. When adding a Group, drag its initial rectangle before entering its name and color. Keyboard users can focus the canvas and press `Shift+F10` or the Context Menu key.
- Click a Node or Group for its Edit/Delete popup. Drag Nodes directly; drag a Group header to move the Group and its Nodes, and drag its corner to resize it. When the resize handle is focused, use arrow keys (`Shift` for larger steps). Double-click a Group header to collapse or expand it.
- Connections are created by dragging the red handle from the source Node to the target Node. With a keyboard, activate the handle and then activate the target Node. Click a connection, then use the line menu for solid/dashed or the arrow menu for no arrow, one-way and two-way. A one-way arrow follows the original drag direction.
- Hover a Node to highlight its direct relatives and their connections in neon red. Click the Node to lock that focus; click empty space or press `Escape` to clear it.
- `Delete` or `Backspace` removes the selected entity after confirmation. Right-clicking a connection also exposes its Delete action.
- Search affects visibility only; it does not change data. Automatic layouts remain optional.
- Every System Map change creates a restore point. Open **History** to review who changed the Map and when; Admins can restore an earlier state.
- Deleting a Map moves it to **Trash** for 30 days. Admins can restore it or permanently delete it sooner.

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
