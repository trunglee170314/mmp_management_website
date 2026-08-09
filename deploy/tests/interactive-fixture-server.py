"""Disposable browser QA server: in-memory database only, NEVER production settings.

Run only in a disposable container with no env_file/database/media mounts.
"""
import os
import sys
from pathlib import Path
from datetime import timedelta
from wsgiref.simple_server import make_server

if os.environ.get("POSTGRES_DB"):
    raise RuntimeError("Refusing fixture setup with PostgreSQL configuration")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["DEBUG"] = "1"
sys.argv.append("test")
import django
django.setup()
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.test import Client
from django.utils import timezone
from core.models import RelationshipEdge, RelationshipGroup, RelationshipMap, RelationshipNode, Scope, Task, TimelineGroup, TimelineMilestone, User
from core.relationship_history import save_relationship_map_revision

call_command("migrate", verbosity=0)
owner = User.objects.create_user(username="qa-owner", password="disposable-qa-only", display_name="QA Owner", account_status="active", role="admin")
other = User.objects.create_user(username="qa-other", password="disposable-qa-only", display_name="QA Other", account_status="active", role="admin")
scope = Scope.objects.create(name="QA Scope")
group = TimelineGroup.objects.create(name="QA Group", color="#14B8A6")
today = timezone.localdate()
task = Task.objects.create(title="QA draggable task", link_url="https://example.test/qa", timeline_start_date=today, due_date=today+timedelta(days=4), timeline_group=group, assignee=owner, created_by=owner)
task.scopes.add(scope)
TimelineMilestone.objects.create(name="QA important release", date=today+timedelta(days=5), task=task, is_important=True, created_by=owner)
TimelineMilestone.objects.create(name="QA small milestone", date=today+timedelta(days=2), created_by=owner)
TimelineMilestone.objects.create(name="QA private secret", date=today+timedelta(days=3), visibility="private", created_by=other)
map = RelationshipMap.objects.create(name="QA System Map", created_by=owner)
map_group = RelationshipGroup.objects.create(relationship_map=map, name="QA Components", color="#14B8A6", x=30, y=30, width=700, height=350)
a = RelationshipNode.objects.create(relationship_map=map, group=map_group, name="QA node one", description="Description-only searchable needle123\nSecond line", color="#3B82F6", x=60, y=110)
b = RelationshipNode.objects.create(relationship_map=map, group=map_group, name="QA node two", color="#22C55E", x=400, y=110)
RelationshipEdge.objects.create(relationship_map=map, source=a, target=b, direction="forward")
save_relationship_map_revision(map, owner, "QA initial fixture")
client = Client()
client.force_login(owner)
print("QA_SESSION=" + client.cookies["sessionid"].value, flush=True)
print("QA_READY", flush=True)
make_server("0.0.0.0", 8000, StaticFilesHandler(get_wsgi_application())).serve_forever()
