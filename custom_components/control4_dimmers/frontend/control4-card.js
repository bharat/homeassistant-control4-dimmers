/**
 * Control4 Dimmers — Lovelace Card
 *
 * Entity-bound card: configure via { type: "custom:control4-card", entity: "select.xxx_device_type" }
 * The entity's attributes provide the IEEE address; the card fetches full device info via websocket.
 */

const DOMAIN = "control4_dimmers";
const CARD_TAG = "control4-card";
const EDITOR_TAG = "control4-card-editor";

/* ────────────────────── version sync ────────────────────── */

const RESOURCE_URL = (() => {
  try {
    const scripts = document.querySelectorAll(
      'script[src*="control4-card"], link[href*="control4-card"]'
    );
    for (const s of scripts) {
      const src = s.src || s.href;
      if (src) return src;
    }
    return new URL(import.meta.url).href;
  } catch {
    return "";
  }
})();
const CARD_VERSION = (() => {
  try {
    return new URL(RESOURCE_URL).searchParams.get("v") || "0.0.0";
  } catch {
    return "0.0.0";
  }
})();

/* ────────────────────── constants ────────────────────── */

const DEVICE_TYPES = {
  dimmer: { label: "Dimmer", model: "C4-APD120", slots: [1, 4], fixedLayout: true },
  keypaddim: { label: "Keypad Dimmer", model: "C4-KD120", slots: [0,1,2,3,4,5], fixedLayout: false },
  keypad: { label: "Keypad", model: "C4-KC120277", slots: [0,1,2,3,4,5], fixedLayout: false },
};

const BEHAVIORS = [
  { value: "keypad", label: "Keypad" },
  { value: "toggle_load", label: "Toggle Load" },
  { value: "load_on", label: "Load On" },
  { value: "load_off", label: "Load Off" },
];

const LED_MODES = [
  { value: "follow_load", label: "Follow Load" },
  { value: "follow_connection", label: "Follow Connection" },
  { value: "push_release", label: "Push/Release" },
  { value: "programmed", label: "Programmed" },
];

const DEFAULT_COLORS = { on: "0000ff", off: "000000" };

/* ────────────────────── styles ────────────────────── */

const CARD_STYLES = `
  :host {
    display: block;
    --c4-slot-height: 32px;
    --c4-chassis-width: 120px;
  }

  ha-card { padding: 16px; }

  /* ── Header ── */

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
    gap: 12px;
  }
  .card-header h2 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 500;
    color: var(--primary-text-color);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .card-header .type-select {
    padding: 4px 8px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
    font-size: 0.8rem;
    font-family: inherit;
    outline: none;
    flex-shrink: 0;
  }
  .card-header .type-select:focus {
    border-color: var(--primary-color);
  }

  /* ── Not configured / no entity ── */

  .no-entity {
    text-align: center;
    padding: 24px 16px;
    color: var(--secondary-text-color);
    font-size: 0.9rem;
  }

  /* ── Main layout ── */

  .device-layout {
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }

  /* ── Chassis (vertical button strip) ── */

  .chassis {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: var(--c4-chassis-width);
    flex-shrink: 0;
    background: var(--secondary-background-color);
    border-radius: var(--ha-card-border-radius, 12px);
    padding: 5px;
  }
  .chassis-slot {
    height: var(--c4-slot-height);
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: box-shadow 0.15s ease;
    font-size: 0.7rem;
    font-weight: 500;
    color: var(--primary-text-color);
    position: relative;
    user-select: none;
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color);
    padding-left: 16px;
  }
  .chassis-slot:hover {
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  }
  .chassis-slot.selected {
    border-color: var(--primary-color);
    box-shadow: 0 0 0 1px var(--primary-color);
  }
  .chassis-slot.size-2 {
    height: calc(var(--c4-slot-height) * 2 + 2px);
  }
  .chassis-slot.size-3 {
    height: calc(var(--c4-slot-height) * 3 + 4px);
  }
  .chassis-slot .slot-label {
    font-size: 0.7rem;
    color: var(--primary-text-color);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .chassis-slot .led-indicator {
    position: absolute;
    left: 5px;
    top: 50%;
    transform: translateY(-50%);
    width: 8px;
    height: 8px;
    border-radius: 50%;
    border: 1px solid var(--divider-color);
  }

  /* ── Config panel ── */

  .config-panel {
    flex: 1;
    min-width: 0;
  }

  .slot-config {
    border-radius: 10px;
    padding: 10px 12px;
    background: var(--secondary-background-color);
  }

  .config-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  .config-row:last-child {
    margin-bottom: 0;
  }
  .config-row label {
    font-size: 0.78rem;
    font-weight: 500;
    color: var(--secondary-text-color);
    width: 60px;
    flex-shrink: 0;
  }
  .config-row input[type="text"],
  .config-row select {
    flex: 1;
    padding: 6px 8px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--input-fill-color, var(--card-background-color, #fff));
    color: var(--primary-text-color);
    font-size: 0.82rem;
    font-family: inherit;
    outline: none;
    min-width: 0;
  }
  .config-row input[type="text"]:focus,
  .config-row select:focus {
    border-color: var(--primary-color);
  }
  .config-row input[type="color"] {
    width: 32px;
    height: 26px;
    padding: 0;
    border: 1px solid var(--divider-color);
    border-radius: 6px;
    cursor: pointer;
    background: transparent;
  }
  .config-row input[type="color"]::-webkit-color-swatch-wrapper { padding: 2px; }
  .config-row input[type="color"]::-webkit-color-swatch { border: none; border-radius: 4px; }

  .color-pair {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
  }
  .color-pair .color-label {
    font-size: 0.72rem;
    color: var(--secondary-text-color);
  }

  /* ── Slot size toolbar ── */

  .layout-toolbar {
    display: flex;
    gap: 4px;
    margin-bottom: 10px;
    align-items: center;
  }
  .layout-toolbar .toolbar-label {
    font-size: 0.78rem;
    color: var(--secondary-text-color);
    margin-right: 2px;
  }
  .layout-toolbar button {
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    font-size: 0.78rem;
    font-family: inherit;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .layout-toolbar button:hover {
    border-color: var(--primary-color);
    color: var(--primary-color);
  }
  .layout-toolbar button.active {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .layout-toolbar button:disabled {
    opacity: 0.4;
    cursor: default;
  }

  /* ── Save / Reset bar ── */

  .save-bar {
    margin-top: 12px;
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .save-bar button {
    padding: 6px 16px;
    border-radius: 8px;
    border: none;
    font-size: 0.82rem;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    transition: opacity 0.15s ease;
  }
  .save-bar .btn-save {
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .save-bar .btn-save:hover { opacity: 0.9; }
  .save-bar .btn-save:disabled { opacity: 0.4; cursor: default; }
  .save-bar .btn-reset {
    background: transparent;
    color: var(--primary-text-color);
    border: 1px solid var(--divider-color);
  }
  .save-bar .btn-reset:disabled { opacity: 0.4; cursor: default; }

  /* ── Version mismatch ── */

  .version-mismatch {
    background: var(--warning-color, #ffc107);
    color: var(--primary-text-color);
    padding: 8px 12px;
    border-radius: 8px;
    margin-bottom: 10px;
    font-size: 0.78rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .version-mismatch button {
    background: rgba(0,0,0,0.1);
    border: none;
    color: inherit;
    padding: 3px 10px;
    border-radius: 5px;
    cursor: pointer;
    font-size: 0.78rem;
    font-weight: 500;
    font-family: inherit;
  }

  /* ── Empty state ── */

  .setup-prompt {
    text-align: center;
    padding: 16px;
  }
  .setup-prompt p {
    color: var(--secondary-text-color);
    font-size: 0.82rem;
    margin: 0;
  }
`;

/* ────────────────────── helpers ────────────────────── */

function hexToInputColor(hex) {
  if (!hex || hex === "000000") return "#000000";
  return "#" + hex.replace("#", "").padStart(6, "0");
}

function inputColorToHex(val) {
  return val.replace("#", "").toLowerCase();
}

function computeLayout(slotConfigs, deviceType) {
  const meta = DEVICE_TYPES[deviceType];
  if (!meta) return [];
  const activeSlots = meta.slots;

  if (meta.fixedLayout) {
    return activeSlots.map((id) => {
      const cfg = slotConfigs.find((s) => s.slot_id === id) || {
        slot_id: id, size: 1,
        name: id === 1 ? "Top" : "Bottom",
        led_on_color: "ffffff", led_off_color: "0000ff",
      };
      return { startSlot: id, size: 1, slots: [cfg] };
    });
  }

  const buttons = [];
  const used = new Set();
  const sorted = [...slotConfigs].sort((a, b) => a.slot_id - b.slot_id);
  for (const cfg of sorted) {
    if (used.has(cfg.slot_id) || !activeSlots.includes(cfg.slot_id)) continue;
    const btn = { startSlot: cfg.slot_id, size: cfg.size || 1, slots: [cfg] };
    used.add(cfg.slot_id);
    for (let i = 1; i < btn.size; i++) used.add(cfg.slot_id + i);
    buttons.push(btn);
  }

  for (const id of activeSlots) {
    if (!used.has(id)) {
      buttons.push({
        startSlot: id, size: 1,
        slots: [{ slot_id: id, size: 1, name: `Button ${id}`, behavior: "keypad", led_mode: "programmed", led_on_color: DEFAULT_COLORS.on, led_off_color: DEFAULT_COLORS.off }],
      });
    }
  }

  buttons.sort((a, b) => a.startSlot - b.startSlot);
  return buttons;
}

function defaultSlotsForType(deviceType) {
  const meta = DEVICE_TYPES[deviceType];
  if (!meta) return [];
  if (deviceType === "dimmer") {
    return [
      { slot_id: 1, size: 1, name: "Top", behavior: "toggle_load", led_mode: "follow_load", led_on_color: "ffffff", led_off_color: "000000" },
      { slot_id: 4, size: 1, name: "Bottom", behavior: "toggle_load", led_mode: "follow_load", led_on_color: "000000", led_off_color: "0000ff" },
    ];
  }
  return meta.slots.map((id) => ({
    slot_id: id, size: 1, name: `Button ${id}`,
    behavior: "keypad", led_mode: "programmed",
    led_on_color: DEFAULT_COLORS.on, led_off_color: DEFAULT_COLORS.off,
  }));
}

/* ────────────────────── main card ────────────────────── */

class Control4Card extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._deviceInfo = null;
    this._selectedSlotId = null;
    this._localSlots = [];
    this._dirty = false;
    this._saving = false;
    this._versionChecked = false;
    this._lastEntityState = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;

    if (first) {
      this._checkVersion();
    }

    // React to entity state changes
    const entityId = this._config.entity;
    if (entityId && hass.states[entityId]) {
      const newState = hass.states[entityId].state;
      const ieee = hass.states[entityId].attributes?.ieee_address;
      if (newState !== this._lastEntityState || (!this._deviceInfo && ieee)) {
        this._lastEntityState = newState;
        if (ieee) this._fetchDevice(ieee);
      }
    }

    if (first) this._render();
  }

  setConfig(config) {
    if (!config) throw new Error("No configuration provided");
    this._config = config;
    this._deviceInfo = null;
    this._localSlots = [];
    this._dirty = false;

    // If we already have hass, kick off device fetch
    if (this._hass && config.entity) {
      const state = this._hass.states[config.entity];
      if (state?.attributes?.ieee_address) {
        this._fetchDevice(state.attributes.ieee_address);
      }
    }
    this._render();
  }

  getCardSize() {
    return 4;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig() {
    return { entity: "" };
  }

  async _checkVersion() {
    if (!this._hass || this._versionChecked) return;
    this._versionChecked = true;
    try {
      const resp = await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/version`,
      });
      const bv = resp?.version || "0.0.0";
      if (bv !== CARD_VERSION && CARD_VERSION !== "0.0.0") {
        this._showVersionMismatch = true;
        this._backendVersion = bv;
        this._render();
      }
    } catch { /* skip */ }
  }

  async _fetchDevice(ieee) {
    if (!this._hass || !ieee) return;
    try {
      const info = await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/device_by_entity`,
        entity_id: this._config.entity,
      });
      this._deviceInfo = info;
      const effectiveType = this._getEffectiveType();
      if (!this._dirty) {
        if (info.config?.slots?.length > 0) {
          this._localSlots = JSON.parse(JSON.stringify(info.config.slots));
        } else {
          this._localSlots = defaultSlotsForType(effectiveType);
        }
      }
      this._render();
    } catch (err) {
      console.error("Control4 Card: failed to fetch device", err);
    }
  }

  _getEffectiveType() {
    if (!this._deviceInfo) return "keypad";
    return (
      this._deviceInfo.config?.device_type_override ||
      this._deviceInfo.device_type ||
      "keypad"
    );
  }

  _getEntityState() {
    if (!this._hass || !this._config.entity) return null;
    return this._hass.states[this._config.entity] || null;
  }

  _handleSlotClick(slotId) {
    this._selectedSlotId = this._selectedSlotId === slotId ? null : slotId;
    this._render();
  }

  _updateSlot(slotId, field, value) {
    const slot = this._localSlots.find((s) => s.slot_id === slotId);
    if (slot) {
      slot[field] = value;
      this._dirty = true;
      this._render();
    }
  }

  _setSlotSize(startSlot, newSize) {
    const effectiveType = this._getEffectiveType();
    const meta = DEVICE_TYPES[effectiveType];
    if (!meta || meta.fixedLayout) return;

    const maxSlot = Math.max(...meta.slots);
    if (startSlot + newSize - 1 > maxSlot) return;

    this._localSlots = this._localSlots.filter(
      (s) => s.slot_id < startSlot || s.slot_id >= startSlot + newSize
    );

    let mainSlot = this._localSlots.find((s) => s.slot_id === startSlot);
    if (!mainSlot) {
      mainSlot = {
        slot_id: startSlot, size: newSize, name: `Button ${startSlot}`,
        behavior: "keypad", led_mode: "programmed",
        led_on_color: DEFAULT_COLORS.on, led_off_color: DEFAULT_COLORS.off,
      };
      this._localSlots.push(mainSlot);
    }
    mainSlot.size = newSize;

    for (const id of meta.slots) {
      if (!this._localSlots.find((s) => s.slot_id === id) && (id < startSlot || id >= startSlot + newSize)) {
        this._localSlots.push({
          slot_id: id, size: 1, name: `Button ${id}`,
          behavior: "keypad", led_mode: "programmed",
          led_on_color: DEFAULT_COLORS.on, led_off_color: DEFAULT_COLORS.off,
        });
      }
    }

    this._localSlots.sort((a, b) => a.slot_id - b.slot_id);
    this._dirty = true;
    this._selectedSlotId = startSlot;
    this._render();
  }

  async _handleTypeChange(newType) {
    if (!this._deviceInfo || !this._hass || !newType) return;
    // Update the select entity directly via HA service
    try {
      await this._hass.callService("select", "select_option", {
        entity_id: this._config.entity,
        option: newType,
      });
      this._localSlots = defaultSlotsForType(newType);
      this._dirty = true;
      this._selectedSlotId = null;
      // Re-fetch to get updated config
      setTimeout(() => {
        const ieee = this._deviceInfo?.ieee_address;
        if (ieee) this._fetchDevice(ieee);
      }, 500);
    } catch (err) {
      console.error("Failed to set device type", err);
    }
  }

  async _handleSave() {
    if (!this._deviceInfo || !this._hass || this._saving) return;
    this._saving = true;
    this._render();
    try {
      await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/device_config`,
        ieee_address: this._deviceInfo.ieee_address,
        slots: this._localSlots,
      });
      this._dirty = false;
      await this._fetchDevice(this._deviceInfo.ieee_address);
    } catch (err) {
      console.error("Failed to save config", err);
    } finally {
      this._saving = false;
      this._render();
    }
  }

  _handleReset() {
    if (!this._deviceInfo) return;
    const effectiveType = this._getEffectiveType();
    if (this._deviceInfo.config?.slots?.length > 0) {
      this._localSlots = JSON.parse(JSON.stringify(this._deviceInfo.config.slots));
    } else {
      this._localSlots = defaultSlotsForType(effectiveType);
    }
    this._dirty = false;
    this._render();
  }

  async _handleReload() {
    const cacheNames = await caches.keys();
    await Promise.all(cacheNames.map((name) => caches.delete(name)));
    window.location.reload();
  }

  /* ── rendering ── */

  _render() {
    if (!this.shadowRoot) return;

    const dev = this._deviceInfo;
    const effectiveType = this._getEffectiveType();
    const layout = computeLayout(this._localSlots, effectiveType);
    const entityState = this._getEntityState();

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        ${this._showVersionMismatch ? `
          <div class="version-mismatch">
            <span>Update available (v${this._backendVersion})</span>
            <button id="reload-btn">Reload</button>
          </div>
        ` : ""}

        ${!this._config.entity ? `
          <div class="no-entity">
            <p>No entity configured. Edit this card and select a Control4 device type entity.</p>
          </div>
        ` : !dev ? `
          <div class="no-entity">
            <p>Loading device...</p>
          </div>
        ` : this._renderDevice(dev, effectiveType, layout, entityState)}
      </ha-card>
    `;

    this._attachListeners();
  }

  _renderDevice(dev, effectiveType, layout, entityState) {
    const typeMeta = DEVICE_TYPES[effectiveType] || DEVICE_TYPES.keypad;
    const selectedSlot = this._localSlots.find((s) => s.slot_id === this._selectedSlotId);
    const detectedType = entityState?.attributes?.detected_type || dev.device_type;

    return `
      <div class="card-header">
        <h2>${dev.friendly_name}</h2>
        <select class="type-select" id="type-select">
          ${Object.entries(DEVICE_TYPES).map(([key, val]) => `
            <option value="${key}" ${effectiveType === key ? "selected" : ""}>
              ${val.label}
            </option>
          `).join("")}
        </select>
      </div>

      <div class="device-layout">
        <div class="chassis">
          ${layout.map((btn) => this._renderChassisSlot(btn)).join("")}
        </div>

        <div class="config-panel">
          ${!typeMeta.fixedLayout ? `
            <div class="layout-toolbar">
              <span class="toolbar-label">Size:</span>
              ${[1, 2, 3].map((size) => `
                <button class="size-btn ${selectedSlot?.size === size ? "active" : ""}"
                        data-size="${size}"
                        ${!selectedSlot || typeMeta.fixedLayout ? "disabled" : ""}>
                  ${size}-slot
                </button>
              `).join("")}
            </div>
          ` : ""}

          ${selectedSlot ? this._renderSlotConfig(selectedSlot, effectiveType) : `
            <div class="setup-prompt">
              <p>Select a button to configure it.</p>
            </div>
          `}
        </div>
      </div>

      <div class="save-bar">
        <button class="btn-reset" id="reset-btn" ${!this._dirty ? "disabled" : ""}>Reset</button>
        <button class="btn-save" id="save-btn" ${!this._dirty || this._saving ? "disabled" : ""}>
          ${this._saving ? "Saving..." : "Save"}
        </button>
      </div>
    `;
  }

  _renderChassisSlot(btn) {
    const cfg = btn.slots[0];
    const isSelected = this._selectedSlotId === btn.startSlot;
    const onColor = cfg.led_on_color || DEFAULT_COLORS.on;

    return `
      <div class="chassis-slot size-${btn.size} ${isSelected ? "selected" : ""}"
           data-slot="${btn.startSlot}">
        <div class="led-indicator" style="background: #${onColor};"></div>
        <span class="slot-label">${cfg.name || `Btn ${btn.startSlot}`}</span>
      </div>
    `;
  }

  _renderSlotConfig(slot, effectiveType) {
    const showLoadOptions = effectiveType !== "keypad";

    return `
      <div class="slot-config">
        <div class="config-row">
          <label>Name</label>
          <input type="text" id="slot-name" value="${slot.name || ""}"
                 placeholder="Button ${slot.slot_id}">
        </div>
        <div class="config-row">
          <label>Behavior</label>
          <select id="slot-behavior">
            ${BEHAVIORS.filter((b) => showLoadOptions || b.value === "keypad").map((b) => `
              <option value="${b.value}" ${slot.behavior === b.value ? "selected" : ""}>${b.label}</option>
            `).join("")}
          </select>
        </div>
        <div class="config-row">
          <label>LED Mode</label>
          <select id="slot-led-mode">
            ${LED_MODES.map((m) => `
              <option value="${m.value}" ${slot.led_mode === m.value ? "selected" : ""}>${m.label}</option>
            `).join("")}
          </select>
        </div>
        <div class="config-row">
          <label>Colors</label>
          <div class="color-pair">
            <span class="color-label">On:</span>
            <input type="color" id="slot-on-color" value="${hexToInputColor(slot.led_on_color)}">
            <span class="color-label">Off:</span>
            <input type="color" id="slot-off-color" value="${hexToInputColor(slot.led_off_color)}">
          </div>
        </div>
      </div>
    `;
  }

  /* ── event listeners ── */

  _attachListeners() {
    const root = this.shadowRoot;
    if (!root) return;

    const reloadBtn = root.getElementById("reload-btn");
    if (reloadBtn) reloadBtn.addEventListener("click", () => this._handleReload());

    const typeSelect = root.getElementById("type-select");
    if (typeSelect) {
      typeSelect.addEventListener("change", (e) => this._handleTypeChange(e.target.value));
    }

    const slots = root.querySelectorAll(".chassis-slot");
    for (const slot of slots) {
      slot.addEventListener("click", () => {
        this._handleSlotClick(parseInt(slot.dataset.slot, 10));
      });
    }

    const sizeBtns = root.querySelectorAll(".size-btn");
    for (const btn of sizeBtns) {
      btn.addEventListener("click", () => {
        if (this._selectedSlotId != null) {
          this._setSlotSize(this._selectedSlotId, parseInt(btn.dataset.size, 10));
        }
      });
    }

    const nameInput = root.getElementById("slot-name");
    if (nameInput) nameInput.addEventListener("input", (e) => this._updateSlot(this._selectedSlotId, "name", e.target.value));

    const behaviorSel = root.getElementById("slot-behavior");
    if (behaviorSel) behaviorSel.addEventListener("change", (e) => this._updateSlot(this._selectedSlotId, "behavior", e.target.value));

    const ledModeSel = root.getElementById("slot-led-mode");
    if (ledModeSel) ledModeSel.addEventListener("change", (e) => this._updateSlot(this._selectedSlotId, "led_mode", e.target.value));

    const onColor = root.getElementById("slot-on-color");
    if (onColor) onColor.addEventListener("input", (e) => this._updateSlot(this._selectedSlotId, "led_on_color", inputColorToHex(e.target.value)));

    const offColor = root.getElementById("slot-off-color");
    if (offColor) offColor.addEventListener("input", (e) => this._updateSlot(this._selectedSlotId, "led_off_color", inputColorToHex(e.target.value)));

    const saveBtn = root.getElementById("save-btn");
    if (saveBtn) saveBtn.addEventListener("click", () => this._handleSave());

    const resetBtn = root.getElementById("reset-btn");
    if (resetBtn) resetBtn.addEventListener("click", () => this._handleReset());
  }
}

/* ────────────────────── card editor ────────────────────── */

class Control4CardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  _getC4Entities() {
    if (!this._hass) return [];
    return Object.keys(this._hass.states)
      .filter((eid) => {
        if (!eid.startsWith("select.")) return false;
        const state = this._hass.states[eid];
        return state?.attributes?.ieee_address != null;
      })
      .map((eid) => ({
        entity_id: eid,
        friendly_name: this._hass.states[eid].attributes.friendly_name || eid,
        device_type: this._hass.states[eid].state,
      }));
  }

  _render() {
    if (!this.shadowRoot) return;
    const entities = this._getC4Entities();

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .editor { padding: 8px 0; }
        label {
          display: block;
          font-size: 0.82rem;
          margin-bottom: 4px;
          color: var(--secondary-text-color);
        }
        select {
          width: 100%;
          padding: 8px;
          border-radius: 8px;
          border: 1px solid var(--divider-color);
          background: var(--input-fill-color, var(--secondary-background-color));
          color: var(--primary-text-color);
          font-size: 0.875rem;
          font-family: inherit;
          outline: none;
        }
        select:focus { border-color: var(--primary-color); }
        .hint {
          margin-top: 6px;
          font-size: 0.75rem;
          color: var(--secondary-text-color);
        }
      </style>
      <div class="editor">
        <label>Control4 Device</label>
        <select id="entity-select">
          <option value="">-- Select a device --</option>
          ${entities.map((e) => `
            <option value="${e.entity_id}" ${this._config.entity === e.entity_id ? "selected" : ""}>
              ${e.friendly_name} (${e.device_type || "unknown"})
            </option>
          `).join("")}
        </select>
        ${entities.length === 0 ? `
          <p class="hint">No Control4 devices found. Make sure the integration is set up and Z2M is running.</p>
        ` : ""}
      </div>
    `;

    const sel = this.shadowRoot.getElementById("entity-select");
    if (sel) {
      sel.addEventListener("change", (e) => {
        this._config = { ...this._config, entity: e.target.value };
        this.dispatchEvent(
          new CustomEvent("config-changed", { detail: { config: this._config } })
        );
      });
    }
  }
}

/* ────────────────────── registration ────────────────────── */

customElements.define(CARD_TAG, Control4Card);
customElements.define(EDITOR_TAG, Control4CardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TAG,
  name: "Control4 Dimmers",
  description: "Configure Control4 dimmers and keypads with a visual slot editor.",
  preview: true,
});

console.info(
  "%c CONTROL4-CARD %c loaded v" + CARD_VERSION,
  "color:#fff;background:#0a84ff;font-weight:bold;padding:2px 6px;border-radius:4px",
  ""
);
