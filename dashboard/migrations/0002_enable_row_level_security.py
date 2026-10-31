from django.db import migrations

# The Data API must see nothing. There are no policies on purpose: Django connects
# as the table owner, and owners bypass row level security unless it is forced.
TABLES = ("articles", "source_status", "headlines", "editions", "edition_headlines",
          "collection_runs", "dashboard_outletsuggestion", "django_migrations")


def _set_row_level_security(schema_editor, action):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    for table in TABLES:
        schema_editor.execute(f"ALTER TABLE {connection.ops.quote_name(table)} {action} ROW LEVEL SECURITY")


def enable(apps, schema_editor):
    _set_row_level_security(schema_editor, "ENABLE")


def disable(apps, schema_editor):
    _set_row_level_security(schema_editor, "DISABLE")


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0001_initial"),
        ("news", "0001_initial"),
    ]
    operations = [migrations.RunPython(enable, disable)]
