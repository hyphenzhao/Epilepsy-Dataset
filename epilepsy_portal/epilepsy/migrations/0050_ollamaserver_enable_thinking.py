from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("epilepsy", "0049_ollamaserver"),
    ]

    operations = [
        migrations.AddField(
            model_name="ollamaserver",
            name="enable_thinking",
            field=models.BooleanField(default=True, verbose_name="启用推理输出"),
        ),
    ]
