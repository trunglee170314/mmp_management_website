from .models import Task


def open_action_count_blocking_done(task, previous_status, next_status):
    """Return open AIs that block a transition into Done."""
    if not task.pk or previous_status == Task.Status.DONE or next_status != Task.Status.DONE:
        return 0
    return task.action_items.filter(is_completed=False).count()


def action_item_would_be_open_on_done_task(task, is_completed):
    """True when an Action Item mutation would violate the Done invariant."""
    return bool(task and task.status == Task.Status.DONE and not is_completed)
