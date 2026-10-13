(function () {
  'use strict';

  function initializePicker(picker) {
    if (picker.dataset.modelPickerInitialized === 'true') return;
    const form = picker.closest('form');
    const input = form && form.elements.namedItem(picker.dataset.fieldName);
    const select = picker.querySelector('[data-model-select]');
    const button = picker.querySelector('[data-model-fetch]');
    const manual = picker.querySelector('[data-model-manual]');
    const status = picker.querySelector('[data-model-status]');
    if (!input || !select || !button || !manual || !status) return;
    picker.dataset.modelPickerInitialized = 'true';

    const roleConfig = picker.closest('[data-llm-config-role]');
    const serverFields = picker.dataset.serverFields === 'true';
    const fieldNames = roleConfig ? {
      provider: roleConfig.dataset.providerField,
      api_base_url: roleConfig.dataset.baseUrlField,
      api_key: roleConfig.dataset.apiKeyField,
      clear_api_key: roleConfig.dataset.clearApiKeyField,
    } : (serverFields ? { provider: 'provider', api_base_url: 'api_base_url', api_key: 'api_key', clear_api_key: 'clear_api_key', ip: 'ip', port: 'port' } : {});
    const sourceFields = Object.values(fieldNames).map(name => form.elements.namedItem(name)).filter(Boolean);
    const inheritedFields = roleConfig && roleConfig.dataset.llmConfigRole === 'thinking'
      ? ['extraction_provider', 'extraction_api_base_url', 'extraction_api_key', 'clear_extraction_api_key']
          .map(name => form.elements.namedItem(name)).filter(Boolean)
      : [];
    sourceFields.push(...inheritedFields);
    const fieldValue = field => field.type === 'checkbox' ? (field.checked ? '1' : '0') : field.value.trim();
    const sourceKey = () => sourceFields.map(fieldValue).join('\n');
    let currentSource = sourceKey();
    let models = [];
    let loaded = false;
    let manualMode = !input.value;
    let requestId = 0;
    let controller = null;

    function showStatus(message, error) {
      status.textContent = message;
      status.classList.toggle('text-danger', Boolean(error));
      status.classList.toggle('text-muted', !error);
    }

    function addOption(value, label) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    }

    function rebuildChoices() {
      const current = input.value;
      select.replaceChildren();
      models.forEach(model => addOption(model, model));
      if (current && !models.includes(current)) {
        addOption(current, current + (loaded ? '（当前值，未在列表中）' : '（当前值）'));
      }
      addOption('', '手动输入模型名称');
      select.value = manualMode ? '' : current;
      manual.hidden = !manualMode;
    }

    select.addEventListener('change', function () {
      manualMode = !select.value;
      manual.hidden = !manualMode;
      if (manualMode) {
        input.focus();
      } else {
        input.value = select.value;
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });

    input.addEventListener('input', function () {
      manualMode = true;
      rebuildChoices();
    });

    function invalidateSource() {
      const nextSource = sourceKey();
      if (nextSource === currentSource) return;
      currentSource = nextSource;
      requestId++;
      if (controller) controller.abort();
      controller = null;
      models = [];
      loaded = false;
      manualMode = true;
      rebuildChoices();
      select.disabled = false;
      button.disabled = false;
      button.textContent = '获取模型';
      showStatus('服务器地址已更改，请重新获取模型；当前输入已保留。');
    }
    sourceFields.forEach(field => {
      field.addEventListener('input', invalidateSource);
      field.addEventListener('change', invalidateSource);
    });

    button.addEventListener('click', async function () {
      const id = ++requestId;
      const requestedSource = sourceKey();
      controller = new AbortController();
      const body = new URLSearchParams();
      const csrfInput = form.elements.namedItem('csrfmiddlewaretoken');
      const csrfToken = csrfInput ? csrfInput.value : '';
      body.set('csrfmiddlewaretoken', csrfToken);
      Object.entries(fieldNames).forEach(([parameter, name]) => {
        const field = form.elements.namedItem(name);
        if (field) body.set(parameter, fieldValue(field));
      });
      if (roleConfig) body.set('config_role', roleConfig.dataset.llmConfigRole);
      inheritedFields.forEach(field => body.set(field.name, fieldValue(field)));
      if (form.dataset.serverId) body.set('server_id', form.dataset.serverId);
      button.disabled = true;
      button.textContent = '获取中…';
      select.disabled = true;
      showStatus('正在获取服务器上的模型…');

      try {
        const response = await fetch(picker.dataset.modelsUrl, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'X-CSRFToken': csrfToken,
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: body.toString(),
          signal: controller.signal,
        });
        const data = await response.json().catch(() => ({}));
        if (id !== requestId || requestedSource !== sourceKey()) return;
        if (!response.ok || data.status !== 'ok' || !Array.isArray(data.models)) {
          throw new Error(data.message || data.error || '获取失败，请检查服务器连接后重试。');
        }
        models = Array.from(new Set(data.models.filter(model => typeof model === 'string' && model.trim())));
        loaded = true;
        rebuildChoices();
        if (!models.length) {
          showStatus('服务器暂无可用模型，可手动输入模型名称或稍后刷新。');
        } else {
          const preserved = input.value && !models.includes(input.value)
            ? ' 当前值未在列表中，已保留。' : '';
          const server = data.server ? ` 来源：${data.server}。` : '';
          showStatus(`已获取 ${models.length} 个模型，请下拉选择。${preserved}${server}`);
        }
      } catch (error) {
        if (id !== requestId || requestedSource !== sourceKey()) return;
        models = [];
        loaded = false;
        manualMode = true;
        rebuildChoices();
        const message = error instanceof TypeError
          ? '获取失败，无法连接服务器，请稍后重试。'
          : (error.message || '获取失败，请检查服务器连接后重试。');
        showStatus(message, true);
      } finally {
        if (id === requestId) {
          controller = null;
          button.disabled = false;
          button.textContent = loaded ? '刷新模型' : '获取模型';
          select.disabled = false;
        }
      }
    });

    rebuildChoices();
  }

  function initialize(root) {
    root.querySelectorAll('[data-llm-server-form]').forEach(form => {
      if (form.dataset.providerInitialized === 'true') return;
      form.dataset.providerInitialized = 'true';
      const provider = form.elements.namedItem('provider');
      if (!provider) return;
      function showProviderFields() {
        const isOllama = !provider.value || provider.value === 'ollama';
        form.querySelectorAll('[data-llm-ollama]').forEach(element => { element.hidden = !isOllama; });
        form.querySelectorAll('[data-llm-api]').forEach(element => { element.hidden = isOllama; });
        const url = form.elements.namedItem('api_base_url');
        if (url) url.placeholder = provider.value === 'anthropic' ? 'https://api.anthropic.com/v1' : 'https://api.openai.com/v1';
      }
      provider.addEventListener('change', showProviderFields);
      showProviderFields();
    });
    root.querySelectorAll('[data-ollama-model-picker]').forEach(initializePicker);
  }

  window.OllamaModelPicker = { initialize };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initialize(document));
  } else {
    initialize(document);
  }
})();
