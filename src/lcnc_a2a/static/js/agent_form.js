// Agent create/edit form — Carbon stepper + provider/preset/mode wiring.
//
// One <form> spans 6 step panes. JS toggles which pane is visible and
// keeps the dependent UI (mode picker, preset switcher, API key source,
// sliders, review summary) in sync. The server-side validator remains
// the source of truth for the actual submit.
(function () {
  "use strict";

  const STEPS = [
    { id: "identity",   label: "Identity"   },
    { id: "model",      label: "Model"      },
    { id: "prompts",    label: "Prompts"    },
    { id: "tools",      label: "Tools"      },
    { id: "guardrails", label: "Guardrails" },
    { id: "review",     label: "Review"     },
  ];

  const PRESETS = {
    openrouter: {
      provider:    "openrouter",
      endpoint:    "https://openrouter.ai/api/v1",
      apiKey:      "openrouter",     // 3 sources, env var name fixed
      envVarFixed: "OPENROUTER_API_KEY",
    },
    localhost: {
      provider: "openai_compatible",
      endpoint: "http://localhost:9121/v1",
      apiKey:   "none",              // no key
    },
    other: {
      provider: "openai_compatible",
      endpoint: "",
      apiKey:   "all",               // 3 sources, env var name user-supplied
    },
  };

  function $(root, sel) { return root.querySelector(sel); }
  function $$(root, sel) { return Array.from(root.querySelectorAll(sel)); }

  function dispatch(el, type) { el.dispatchEvent(new Event(type, { bubbles: true })); }

  // ---------- stepper ----------

  function initStepper(form) {
    const stepperEl = $(form, "[data-stepper]");
    const panesRoot = $(form, "[data-stepper-panes]");
    if (!stepperEl || !panesRoot) return null;

    const items = $$(stepperEl, "[data-step-item]");
    const panes = $$(panesRoot, "[data-step-pane]");
    const backBtn = $(form, "[data-step-back]");
    const nextBtn = $(form, "[data-step-next]");
    const submitBtn = $(form, "[data-step-submit]");
    const nextLabel = $(form, "[data-next-label]");
    const stepCurrent = $(form, "[data-step-current]");

    let activeIndex = 0;

    function activate(index) {
      activeIndex = Math.max(0, Math.min(STEPS.length - 1, index));
      const id = STEPS[activeIndex].id;

      items.forEach((li, i) => {
        li.classList.remove("is-current", "is-incomplete", "is-complete");
        if (i === activeIndex) li.classList.add("is-current");
        else if (i < activeIndex) li.classList.add("is-complete");
        else li.classList.add("is-incomplete");
      });
      panes.forEach((p) => {
        p.classList.toggle("is-active", p.getAttribute("data-step-pane") === id);
      });

      if (backBtn) backBtn.disabled = activeIndex === 0;

      if (activeIndex === STEPS.length - 1) {
        if (nextBtn) nextBtn.hidden = true;
        if (submitBtn) submitBtn.hidden = false;
        renderReview(form);
      } else {
        if (nextBtn) {
          nextBtn.hidden = false;
          if (nextLabel) nextLabel.textContent = "Next: " + STEPS[activeIndex + 1].label;
        }
        if (submitBtn) submitBtn.hidden = true;
      }

      if (stepCurrent) stepCurrent.textContent = String(activeIndex + 1);

      // Scroll the stepper region back into view on jump.
      const top = panesRoot.getBoundingClientRect().top;
      if (top < 0) panesRoot.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    items.forEach((li, i) => {
      const btn = $(li, "[data-step-go]");
      if (btn) btn.addEventListener("click", () => activate(i));
    });
    if (backBtn) backBtn.addEventListener("click", () => activate(activeIndex - 1));
    if (nextBtn) nextBtn.addEventListener("click", () => activate(activeIndex + 1));

    activate(0);
    return { activate, getIndex: () => activeIndex };
  }

  // ---------- mode (select + cards) ----------

  function initMode(form) {
    const select = $(form, "#mode");
    const cards = $$(form, "[data-mode-card]");
    if (!select) return;

    function syncCards(mode) {
      cards.forEach((c) => {
        const isMine = c.getAttribute("data-mode-card") === mode;
        c.classList.toggle("is-selected", isMine);
        // tag swap
        const tagEl = c.querySelector(".cds-tag");
        if (tagEl) {
          tagEl.classList.remove("cds-tag--blue", "cds-tag--outline");
          tagEl.classList.add(isMine ? "cds-tag--blue" : "cds-tag--outline");
        }
      });
    }
    function syncConditional() {
      const mode = select.value;
      $$(form, "[data-show-on-mode]").forEach((el) => {
        const want = el.getAttribute("data-show-on-mode").split(",");
        el.hidden = !want.includes(mode);
      });
    }

    cards.forEach((c) => {
      c.addEventListener("click", () => {
        select.value = c.getAttribute("data-mode-card");
        dispatch(select, "change");
      });
    });
    select.addEventListener("change", () => {
      syncCards(select.value);
      syncConditional();
    });

    syncCards(select.value);
    syncConditional();
  }

  // ---------- preset switcher + API key visibility ----------

  function initPreset(form) {
    const presetSelect = $(form, "#model_preset");
    const presetButtons = $$(form, "[data-preset]");
    const providerInput = $(form, "#model_provider");
    const endpointInput = $(form, "#model_endpoint");
    const apiKeyRow     = $(form, "[data-api-key-row]");
    const apiKeyWrap    = $(form, "[data-api-key-wrap]");
    const envVarWrap    = $(form, "[data-env-var-name-wrap]");
    const envVarInput   = $(form, "#provider_api_key_env_var_name");
    const apiKeyInput   = $(form, "#provider_api_key");

    if (!presetSelect) return;

    function currentPreset() {
      return PRESETS[presetSelect.value] || PRESETS.other;
    }
    function selectedSource() {
      const checked = form.querySelector('input[name="api_key_source"]:checked');
      return checked ? checked.value : "input";
    }

    function syncPresetButtons() {
      presetButtons.forEach((b) => {
        b.classList.toggle("is-selected", b.getAttribute("data-preset") === presetSelect.value);
      });
    }

    function syncFromPreset(prefill) {
      const preset = currentPreset();
      providerInput.value = preset.provider;

      if (prefill) endpointInput.value = preset.endpoint;
      endpointInput.placeholder =
        presetSelect.value === "other" ? "https://your-host/v1" : preset.endpoint;

      // preset-gated blocks (custom HTTP headers).
      $$(form, "[data-show-on-preset]").forEach((el) => {
        const want = el.getAttribute("data-show-on-preset").split(",");
        el.hidden = !want.includes(presetSelect.value);
      });

      // For openrouter, env var name is fixed and the input is not user-editable.
      if (preset.apiKey === "openrouter" && envVarInput) {
        envVarInput.value = preset.envVarFixed || "OPENROUTER_API_KEY";
        envVarInput.readOnly = true;
      } else if (envVarInput) {
        envVarInput.readOnly = false;
      }

      if (preset.apiKey === "none") {
        if (apiKeyRow) apiKeyRow.hidden = true;
        if (apiKeyInput) apiKeyInput.value = "";
        return;
      }
      if (apiKeyRow) apiKeyRow.hidden = false;
      syncApiKeySourceArea();
    }

    function syncApiKeySourceArea() {
      const preset = currentPreset();
      const source = selectedSource();

      if (apiKeyWrap) apiKeyWrap.hidden = source !== "input";

      if (envVarWrap) {
        // For "openrouter" (apiKey === "openrouter") env var name is fixed but still
        // shown read-only when source is env_*. For "other" (apiKey === "all") it's
        // always shown when source is env_*. For "input" it's hidden.
        const wantName = (preset.apiKey === "all" || preset.apiKey === "openrouter") && source !== "input";
        envVarWrap.hidden = !wantName;
        if (envVarInput) {
          envVarInput.required = wantName && preset.apiKey === "all";
          if (wantName && preset.apiKey === "all" && !envVarInput.value) {
            envVarInput.placeholder = "MY_LLM_API_KEY";
          }
        }
      }

      // visual radios
      $$(form, ".cds-radio").forEach((label) => {
        const input = label.querySelector('input[type="radio"]');
        label.classList.toggle("is-checked", !!(input && input.checked));
      });
    }

    presetButtons.forEach((b) => {
      b.addEventListener("click", () => {
        presetSelect.value = b.getAttribute("data-preset");
        dispatch(presetSelect, "change");
      });
    });
    presetSelect.addEventListener("change", () => {
      syncPresetButtons();
      syncFromPreset(true);
    });
    $$(form, 'input[name="api_key_source"]').forEach((r) => {
      r.addEventListener("change", syncApiKeySourceArea);
    });

    syncPresetButtons();
    syncFromPreset(false);
  }

  // ---------- sliders ----------

  function initSliders(form) {
    $$(form, "[data-slider-for]").forEach((range) => {
      const id = range.getAttribute("data-slider-for");
      const num = form.querySelector("#" + CSS.escape(id));
      const display = form.querySelector("#" + CSS.escape(id) + "_display");
      if (!num) return;
      function fromRange() {
        num.value = range.value;
        if (display) display.textContent = range.value;
      }
      function fromNum() {
        range.value = num.value;
        if (display) display.textContent = num.value;
      }
      range.addEventListener("input", fromRange);
      num.addEventListener("input", fromNum);
      if (display) display.textContent = num.value;
    });
  }

  // ---------- char counters ----------

  function initCharCounters(form) {
    $$(form, "[data-char-counter]").forEach((el) => {
      const id = el.getAttribute("data-char-counter");
      const target = form.querySelector("#" + CSS.escape(id));
      if (!target) return;
      function update() {
        const max = target.getAttribute("maxlength");
        const len = (target.value || "").length;
        el.textContent = max ? `${len} / ${max}` : `${len}`;
      }
      target.addEventListener("input", update);
      update();
    });
  }

  // ---------- review pane ----------

  function fmt(v, fallback) {
    if (v == null) return fallback || "—";
    const s = String(v).trim();
    return s.length ? s : (fallback || "—");
  }

  function setText(node, value) {
    if (!node) return;
    node.textContent = fmt(value);
  }

  function renderReview(form) {
    const get = (name) => {
      const el = form.querySelector('[name="' + name + '"]');
      return el ? el.value : "";
    };
    const mode = get("mode");
    const provider = get("model_provider");
    const endpoint = get("model_endpoint");
    const modelId = get("model_id");
    const apiKeySource = (function () {
      const el = form.querySelector('input[name="api_key_source"]:checked');
      return el ? el.value : "input";
    })();
    const envVarName = get("provider_api_key_env_var_name");

    setText(form.querySelector("[data-review-name]"), get("name"));
    const modeNode = form.querySelector("[data-review-mode]");
    if (modeNode) modeNode.textContent = mode || "—";
    setText(form.querySelector("[data-review-description]"), get("description"));
    setText(form.querySelector("[data-review-provider]"), provider);
    setText(form.querySelector("[data-review-endpoint]"), endpoint);
    setText(form.querySelector("[data-review-model-id]"), modelId);

    const apiKeyNode = form.querySelector("[data-review-api-key]");
    if (apiKeyNode) {
      if (apiKeySource === "input") {
        apiKeyNode.innerHTML = '<span class="cds-tag cds-tag--green">stored</span> typed by user';
      } else if (apiKeySource === "env_dynamic") {
        apiKeyNode.innerHTML = '<span class="cds-tag cds-tag--blue">env_dynamic</span> ' +
          (envVarName ? "<code>$" + escapeHtml(envVarName) + "</code>" : "<em>missing env var name</em>");
      } else {
        apiKeyNode.innerHTML = '<span class="cds-tag cds-tag--green">env_snapshot</span> ' +
          (envVarName ? "<code>$" + escapeHtml(envVarName) + "</code>" : "<em>missing env var name</em>");
      }
    }

    const promptsNode = form.querySelector("[data-review-prompts]");
    if (promptsNode) {
      if (mode === "plan_execute") {
        const planner = (get("planner_prompt") || "").length;
        const exec = (get("executor_prompt") || "").length;
        promptsNode.innerHTML =
          '<div class="cds-kv"><span class="cds-kv-k">Planner</span><span class="cds-kv-v">' + planner + ' chars</span></div>' +
          '<div class="cds-kv"><span class="cds-kv-k">Executor</span><span class="cds-kv-v">' + exec + ' chars</span></div>';
      } else {
        const sys = (get("system_prompt") || "").length;
        promptsNode.innerHTML =
          '<div class="cds-kv"><span class="cds-kv-k">System</span><span class="cds-kv-v">' + sys + ' chars</span></div>';
      }
    }

    setText(form.querySelector("[data-review-max-loops]"), get("max_loops"));
    const tokens = parseInt(get("max_tokens"), 10);
    setText(form.querySelector("[data-review-max-tokens]"), Number.isFinite(tokens) ? tokens.toLocaleString() : get("max_tokens"));

    const simRow = form.querySelector("[data-review-similarity-row]");
    const stepsRow = form.querySelector("[data-review-max-steps-row]");
    if (simRow) simRow.style.display = mode === "react" ? "" : "none";
    if (stepsRow) stepsRow.style.display = mode === "plan_execute" ? "" : "none";
    setText(form.querySelector("[data-review-similarity]"), get("similarity_threshold"));
    setText(form.querySelector("[data-review-max-steps]"), get("max_steps"));

    // Catalog selections in the Tools review card (create flow only).
    const toolsNode = form.querySelector("[data-review-tools-presets]");
    if (toolsNode) {
      const checked = $$(form, "[data-preset-checkbox]").filter((cb) => cb.checked);
      if (checked.length === 0) {
        toolsNode.innerHTML = '<span class="cds-helper">No catalog presets selected.</span>';
      } else {
        const items = checked.map((cb) => {
          const card = cb.closest("[data-preset-card]");
          const title = card ? card.querySelector(".cds-tool-title span") : null;
          const name = title ? title.textContent : cb.value;
          return '<li>' + escapeHtml(name) + '</li>';
        }).join("");
        toolsNode.innerHTML = '<ul class="cds-tool-list">' + items + '</ul>';
      }
    }

    const cardNode = form.querySelector("[data-review-card]");
    if (cardNode) {
      const card = {
        name: get("name") || "(unnamed)",
        description: get("description"),
        capabilities: { streaming: true, pushNotifications: false },
        skills: [{ id: "default", name: get("name") || "default", inputModes: ["text"], outputModes: ["text"] }],
        mode: mode,
        model: { provider: provider, endpoint: endpoint, id: modelId },
      };
      cardNode.textContent = JSON.stringify(card, null, 2);
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ---------- bootstrap ----------

  function initPresetCards(form) {
    $$(form, "[data-preset-card]").forEach((card) => {
      const cb = card.querySelector("[data-preset-checkbox]");
      const tag = card.querySelector("[data-preset-tag]");
      if (!cb || !tag) return;
      function sync() {
        if (cb.checked) {
          tag.textContent = "added";
          tag.classList.remove("cds-tag--outline");
          tag.classList.add("cds-tag--blue");
        } else {
          tag.textContent = "available";
          tag.classList.remove("cds-tag--blue");
          tag.classList.add("cds-tag--outline");
        }
      }
      cb.addEventListener("change", sync);
      sync();
    });
  }

  // ---------- validation summary ----------

  function labelFor(field) {
    if (field.id) {
      const lbl = field.form
        ? field.form.querySelector('label[for="' + CSS.escape(field.id) + '"]')
        : null;
      if (lbl) {
        const text = (lbl.textContent || "").replace(/\*$/, "").trim();
        if (text) return text;
      }
    }
    if (field.getAttribute("aria-label")) return field.getAttribute("aria-label");
    return field.name || field.id || "field";
  }

  function paneIndexFor(field) {
    const pane = field.closest("[data-step-pane]");
    if (!pane) return -1;
    const id = pane.getAttribute("data-step-pane");
    return STEPS.findIndex((s) => s.id === id);
  }

  function collectInvalid(scope) {
    return Array.from(scope.querySelectorAll(":invalid")).filter((el) => {
      // skip disabled controls and template inputs without a name
      if (el.disabled) return false;
      return true;
    });
  }

  function initValidationUx(form, stepper) {
    if (!stepper) return;
    const noticeEl = $(form, "[data-form-errors]");
    const listEl = $(form, "[data-form-errors-list]");
    const titleEl = $(form, "[data-form-errors-title]");
    const nextBtn = $(form, "[data-step-next]");

    function hideNotice() {
      if (noticeEl) noticeEl.hidden = true;
    }

    function showSummary(invalids) {
      if (!noticeEl || !listEl) return;
      listEl.innerHTML = "";
      invalids.forEach((field) => {
        const idx = paneIndexFor(field);
        const stepLabel = idx >= 0 ? STEPS[idx].label : "Form";
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = "#";
        a.textContent = stepLabel + " — " + labelFor(field) + ": " + (field.validationMessage || "invalid value");
        a.addEventListener("click", (ev) => {
          ev.preventDefault();
          if (idx >= 0) stepper.activate(idx);
          setTimeout(() => {
            field.focus();
            if (typeof field.reportValidity === "function") field.reportValidity();
          }, 0);
        });
        li.appendChild(a);
        listEl.appendChild(li);
      });
      if (titleEl) {
        titleEl.textContent =
          invalids.length === 1
            ? "1 field needs attention"
            : invalids.length + " fields need attention";
      }
      noticeEl.hidden = false;
      noticeEl.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // Block Next when current pane has invalid fields; show native bubble on first.
    if (nextBtn) {
      nextBtn.addEventListener(
        "click",
        (e) => {
          const panes = $$(form, "[data-step-pane]");
          const current = panes[stepper.getIndex()];
          if (!current) return;
          const invalids = collectInvalid(current);
          if (invalids.length === 0) {
            hideNotice();
            return;
          }
          e.preventDefault();
          e.stopImmediatePropagation();
          invalids[0].focus();
          if (typeof invalids[0].reportValidity === "function") {
            invalids[0].reportValidity();
          }
        },
        true, // capture: run BEFORE the stepper's own click handler
      );
    }

    // Submit: show full summary; never reach the server with an invalid form.
    form.addEventListener("submit", (e) => {
      if (form.checkValidity()) {
        hideNotice();
        return;
      }
      e.preventDefault();
      showSummary(collectInvalid(form));
    });

    // User starts fixing things → hide the summary.
    form.addEventListener("input", hideNotice);
    form.addEventListener("change", hideNotice);
  }

  function initForm(form) {
    const stepper = initStepper(form);
    initMode(form);
    initPreset(form);
    initSliders(form);
    initCharCounters(form);
    initPresetCards(form);
    initValidationUx(form, stepper);
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
