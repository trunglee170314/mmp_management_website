import django.db.models.deletion
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.models import Count


def copy_scope_links(apps, schema_editor):
    Task = apps.get_model("core", "Task")
    through = Task.scopes.through
    batch = []
    rows = Task.objects.filter(scope_id__isnull=False).values_list("pk", "scope_id")
    for task_id, scope_id in rows.iterator(chunk_size=1000):
        batch.append(through(task_id=task_id, scope_id=scope_id))
        if len(batch) == 1000:
            through.objects.bulk_create(batch, batch_size=1000)
            batch.clear()
    if batch:
        through.objects.bulk_create(batch, batch_size=1000)


def restore_single_scope(apps, schema_editor):
    Task = apps.get_model("core", "Task")
    if Task.objects.annotate(scope_count=Count("scopes")).exclude(scope_count=1).exists():
        raise IrreversibleError(
            "Task scopes cannot be rolled back because at least one task does not have "
            "exactly one scope. Restore a database backup instead."
        )

    batch = []
    links = Task.scopes.through.objects.values_list("task_id", "scope_id")
    for task_id, scope_id in links.iterator(chunk_size=1000):
        batch.append(Task(pk=task_id, scope_id=scope_id))
        if len(batch) == 1000:
            Task.objects.bulk_update(batch, ["scope"], batch_size=1000)
            batch.clear()
    if batch:
        Task.objects.bulk_update(batch, ["scope"], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_meeting_task_order"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="scope",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tasks",
                to="core.scope",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="scopes",
            field=models.ManyToManyField(
                related_name="multi_scope_tasks",
                to="core.scope",
            ),
        ),
        migrations.RunPython(copy_scope_links, restore_single_scope),
        migrations.RemoveField(
            model_name="task",
            name="scope",
        ),
        migrations.AlterField(
            model_name="task",
            name="scopes",
            field=models.ManyToManyField(
                related_name="tasks",
                to="core.scope",
            ),
        ),
        migrations.AddIndex(
            model_name="taskhistory",
            index=models.Index(
                fields=["task", "event", "-created_at"],
                name="taskhist_event_idx",
            ),
        ),
    ]
