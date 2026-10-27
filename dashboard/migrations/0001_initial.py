from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="OutletSuggestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=140)),
                ("website", models.URLField(max_length=500)),
                ("country", models.CharField(max_length=100)),
                ("reason", models.TextField(blank=True, max_length=1000)),
                ("submitted_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={"ordering": ["-submitted_at", "-id"]},
        ),
    ]
