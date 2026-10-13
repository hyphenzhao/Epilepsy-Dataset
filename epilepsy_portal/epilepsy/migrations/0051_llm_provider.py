from django.db import migrations, models
import epilepsy.llm_secrets


class Migration(migrations.Migration):
    dependencies = [("epilepsy", "0050_ollamaserver_enable_thinking")]
    operations = [
        migrations.AddField(model_name="ollamaserver", name="provider", field=models.CharField(choices=[("ollama", "Ollama"), ("openai", "OpenAI / 兼容 API"), ("anthropic", "Anthropic")], default="ollama", max_length=20, verbose_name="服务类型")),
        migrations.AddField(model_name="ollamaserver", name="api_base_url", field=models.CharField(blank=True, default="", max_length=500, verbose_name="API 地址")),
        migrations.AddField(model_name="ollamaserver", name="api_key", field=epilepsy.llm_secrets.EncryptedAPIKeyField(blank=True, default="", verbose_name="API Key")),
        migrations.AlterField(model_name="ollamaserver", name="ip", field=models.CharField(blank=True, max_length=255, verbose_name="IP / Host")),
        migrations.AlterModelOptions(name="ollamaserver", options={"ordering": ["-is_enabled", "name", "id"], "verbose_name": "LLM 服务", "verbose_name_plural": "LLM 服务"}),
    ]
