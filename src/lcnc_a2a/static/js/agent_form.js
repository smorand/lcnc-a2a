// Agent create/edit form behaviour: conditional sections + provider presets.
// Pure DOM manipulation; the server-side validator is the source of truth.
(function () {
  const PRESETS = {
    openrouter: {
      provider: "openrouter",
      endpoint: "https://openrouter.ai/api/v1",
      apiKey: "openrouter", // 3 sources, env var name is fixed
      envVarFixed: "OPENROUTER_API_KEY",
    },
    localhost: {
      provider: "openai_compatible",
      endpoint: "http://localhost:9121/v1",
      apiKey: "none", // no key
    },
    other: {
      provider: "openai_compatible",
      endpoint: "",
      apiKey: "all", // 3 sources, env var name is user-supplied
    },
  };

  function initForm(form) {
    const modeSelect = form.querySelector("#mode");
    const presetSelect = form.querySelector("#model_preset");
    const providerInput = form.querySelector("#model_provider");
    const endpointInput = form.querySelector("#model_endpoint");
    const apiKeyRow = form.querySelector("[data-api-key-row]");
    const apiKeyWrap = form.querySelector("[data-api-key-wrap]");
    const apiKeySourceCol = form.querySelector("[data-api-key-source]");
    const envVarNameWrap = form.querySelector("[data-env-var-name-wrap]");
    const envVarNameInput = form.querySelector("#provider_api_key_env_var_name");
    const apiKeyInput = form.querySelector("#provider_api_key");
    const charCounters = form.querySelectorAll("[data-char-counter]");
    if (!modeSelect || !presetSelect || !providerInput || !endpointInput) return;

    function syncFromMode() {
      const mode = modeSelect.value;
      form.querySelectorAll("[data-show-on-mode]").forEach((el) => {
        const want = el.getAttribute("data-show-on-mode").split(",");
        el.hidden = !want.includes(mode);
      });
    }

    function syncFromPreset(prefill) {
      const preset = PRESETS[presetSelect.value] || PRESETS.other;
      providerInput.value = preset.provider;

      if (prefill) {
        endpointInput.value = preset.endpoint;
      }
      endpointInput.placeholder =
        presetSelect.value === "other" ? "https://your-host/v1" : preset.endpoint;

      // Toggle blocks gated on a specific preset (e.g. custom HTTP headers).
      form.querySelectorAll("[data-show-on-preset]").forEach((el) => {
        const want = el.getAttribute("data-show-on-preset").split(",");
        el.hidden = !want.includes(presetSelect.value);
      });

      if (preset.apiKey === "none") {
        if (apiKeyRow) apiKeyRow.hidden = true;
        if (apiKeyInput) apiKeyInput.value = "";
        return;
      }
      if (apiKeyRow) apiKeyRow.hidden = false;
      syncApiKeySourceArea();
    }

    function syncApiKeySourceArea() {
      const preset = PRESETS[presetSelect.value] || PRESETS.other;
      const checked = form.querySelector('input[name="api_key_source"]:checked');
      const source = checked ? checked.value : "input";

      // Input column visible only when source=input.
      if (apiKeyWrap) apiKeyWrap.hidden = source !== "input";

      // env var name field: visible only for "other" preset AND env_*.
      if (envVarNameWrap) {
        const wantName = preset.apiKey === "all" && source !== "input";
        envVarNameWrap.hidden = !wantName;
        if (envVarNameInput) {
          envVarNameInput.required = wantName;
          if (wantName && !envVarNameInput.value) {
            envVarNameInput.placeholder = "MY_LLM_API_KEY";
          }
        }
      }

      // For openrouter, no env var name input needed; clear it so it doesn't get posted.
      if (preset.apiKey === "openrouter" && envVarNameInput) {
        envVarNameInput.value = "";
      }
    }

    function updateCharCounter(el) {
      const targetId = el.getAttribute("data-char-counter");
      const target = form.querySelector("#" + CSS.escape(targetId));
      if (!target) return;
      const max = target.getAttribute("maxlength");
      const len = target.value.length;
      el.textContent = max ? `${len} / ${max}` : `${len}`;
    }

    modeSelect.addEventListener("change", syncFromMode);
    presetSelect.addEventListener("change", () => syncFromPreset(true));
    form
      .querySelectorAll('input[name="api_key_source"]')
      .forEach((r) => r.addEventListener("change", syncApiKeySourceArea));
    charCounters.forEach((el) => {
      const targetId = el.getAttribute("data-char-counter");
      const target = form.querySelector("#" + CSS.escape(targetId));
      if (target) {
        target.addEventListener("input", () => updateCharCounter(el));
      }
      updateCharCounter(el);
    });

    syncFromMode();
    syncFromPreset(false);
  }

  function init() {
    document.querySelectorAll("form[data-agent-form]").forEach(initForm);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
