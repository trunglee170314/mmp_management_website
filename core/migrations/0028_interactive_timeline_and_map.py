from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0027_board_component_name_constraint")]
    operations = [
        migrations.AddField(model_name="timelinemilestone", name="is_important", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="timelinemilestone", name="visibility", field=models.CharField(choices=[("shared", "Shared"), ("private", "Private")], db_index=True, default="shared", max_length=7)),
        migrations.AddField(model_name="timelinemilestone", name="task", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="milestones", to="core.task")),
        migrations.AlterField(model_name="timelinemilestone", name="color", field=models.CharField(blank=True, default="", max_length=7)),
        migrations.AddField(model_name="relationshipnode", name="keep_position", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="auditlog", name="is_private", field=models.BooleanField(db_index=True, default=False)),
        migrations.AddField(model_name="auditlog", name="private_owner", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="private_audit_logs", to=settings.AUTH_USER_MODEL)),
    ]
