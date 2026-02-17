/**
 * Control4 Dimmers — Lovelace Card
 *
 * A visual configuration card for Control4 dimmers and keypads.
 * Shows a 6-slot chassis editor, per-button LED color pickers,
 * button behavior selects, and device type display/override.
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
    const me = new URL(import.meta.url);
    return me.href;
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
  dimmer: { label: "Dimmer (C4-APD120)", slots: [1, 4], fixedLayout: true },
  keypaddim: { label: "Keypad Dimmer (C4-KD120)", slots: [0,1,2,3,4,5], fixedLayout: false },
  keypad: { label: "Keypad (C4-KC120277)", slots: [0,1,2,3,4,5], fixedLayout: false },
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

const DEFAULT_COLORS = {
  on: "0000ff",
  off: "000000",
};

/* ────────────────────── styles ────────────────────── */

const CARD_STYLES = `
  :host {
    display: block;
    --c4-slot-height: 56px;
    --c4-chassis-width: 72px;
  }

  ha-card {
    padding: 16px;
  }

  /* ── Header ── */

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }
  .card-header h2 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 500;
    color: var(--primary-text-color);
  }
  .card-header .device-type {
    font-size: 0.75rem;
    color: var(--secondary-text-color);
  }

  /* ── Device selector (no device chosen) ── */

  .no-device {
    text-align: center;
    padding: 32px 16px;
    color: var(--secondary-text-color);
  }
  .no-device p {
    margin: 0 0 12px;
    font-size: 0.9rem;
  }
  .no-device select {
    padding: 8px 12px;
    border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--divider-color);
    background: var(--input-fill-color, var(--secondary-background-color));
    color: var(--primary-text-color);
    font-size: 0.875rem;
    width: 100%;
    max-width: 320px;
    outline: none;
  }
  .no-device select:focus {
    border-color: var(--primary-color);
  }

  /* ── Main layout ── */

  .device-layout {
    display: flex;
    gap: 16px;
    align-items: flex-start;
  }

  /* ── Chassis (vertical button strip) ── */

  .chassis {
    display: flex;
    flex-direction: column;
    gap: 3px;
    width: var(--c4-chassis-width);
    flex-shrink: 0;
    background: var(--secondary-background-color);
    border-radius: var(--ha-card-border-radius, 12px);
    padding: 6px;
  }
  .chassis-slot {
    height: var(--c4-slot-height);
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: box-shadow 0.2s ease, background 0.2s ease;
    font-size: 0.7rem;
    font-weight: 500;
    color: var(--primary-text-color);
    position: relative;
    user-select: none;
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color);
  }
  .chassis-slot:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }
  .chassis-slot.selected {
    border-color: var(--primary-color);
    box-shadow: 0 0 0 1px var(--primary-color);
  }
  .chassis-slot.size-2 {
    height: calc(var(--c4-slot-height) * 2 + 3px);
  }
  .chassis-slot.size-3 {
    height: calc(var(--c4-slot-height) * 3 + 6px);
  }
  .chassis-slot .slot-label {
    font-size: 0.7rem;
    color: var(--primary-text-color);
  }
  .chassis-slot .led-indicator {
    position: absolute;
    left: 6px;
    top: 50%;
    transform: translateY(-50%);
    width: 10px;
    height: 10px;
    border-radius: 50%;
    border: 1px solid var(--divider-color);
    box-shadow: inset 0 0 4px rgba(0,0,0,0.1);
  }

  /* ── Config panel ── */

  .config-panel {
    flex: 1;
    min-width: 0;
  }

  .slot-config {
    border-radius: var(--ha-card-border-radius, 12px);
    padding: 16px;
    background: var(--secondary-background-color);
  }

  .config-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }
  .config-row:last-child {
    margin-bottom: 0;
  }
  .config-row label {
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--secondary-text-color);
    width: 72px;
    flex-shrink: 0;
  }
  .config-row input[type="text"],
  .config-row select {
    flex: 1;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--input-fill-color, var(--card-background-color, #fff));
    color: var(--primary-text-color);
    font-size: 0.85rem;
    font-family: inherit;
    outline: none;
  }
  .config-row input[type="text"]:focus,
  .config-row select:focus {
    border-color: var(--primary-color);
  }
  .config-row input[type="color"] {
    width: 40px;
    height: 32px;
    padding: 0;
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    cursor: pointer;
    background: transparent;
  }
  .config-row input[type="color"]::-webkit-color-swatch-wrapper {
    padding: 3px;
  }
  .config-row input[type="color"]::-webkit-color-swatch {
    border: none;
    border-radius: 5px;
  }

  .color-pair {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
  }
  .color-pair .color-label {
    font-size: 0.75rem;
    color: var(--secondary-text-color);
  }

  /* ── Slot size toolbar ── */

  .layout-toolbar {
    display: flex;
    gap: 6px;
    margin-bottom: 16px;
    align-items: center;
  }
  .layout-toolbar .toolbar-label {
    font-size: 0.8rem;
    color: var(--secondary-text-color);
    margin-right: 4px;
  }
  .layout-toolbar button {
    padding: 6px 14px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    font-size: 0.8rem;
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

  /* ── Device type row ── */

  .type-override {
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .type-override label {
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--secondary-text-color);
  }
  .type-override select {
    padding: 6px 10px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--input-fill-color, var(--secondary-background-color));
    color: var(--primary-text-color);
    font-size: 0.85rem;
    font-family: inherit;
    outline: none;
  }
  .type-override select:focus {
    border-color: var(--primary-color);
  }

  /* ── Save / Reset bar ── */

  .save-bar {
    margin-top: 16px;
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .save-bar button {
    padding: 8px 20px;
    border-radius: 8px;
    border: none;
    font-size: 0.875rem;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    transition: opacity 0.15s ease;
  }
  .save-bar .btn-save {
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .save-bar .btn-save:hover {
    opacity: 0.9;
  }
  .save-bar .btn-save:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .save-bar .btn-reset {
    background: transparent;
    color: var(--primary-text-color);
    border: 1px solid var(--divider-color);
  }
  .save-bar .btn-reset:disabled {
    opacity: 0.4;
    cursor: default;
  }

  /* ── Version mismatch ── */

  .version-mismatch {
    background: var(--warning-color, #ffc107);
    color: var(--primary-text-color);
    padding: 10px 14px;
    border-radius: var(--ha-card-border-radius, 12px);
    margin-bottom: 12px;
    font-size: 0.8rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .version-mismatch button {
    background: rgba(0,0,0,0.1);
    border: none;
    color: inherit;
    padding: 4px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.8rem;
    font-weight: 500;
    font-family: inherit;
  }

  /* ── Empty state ── */

  .setup-prompt {
    text-align: center;
    padding: 24px 16px;
  }
  .setup-prompt p {
    color: var(--secondary-text-color);
    font-size: 0.875rem;
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

/**
 * Compute slot layout from a flat array of slot configs.
 * Returns an array of "visual buttons": { startSlot, size, slots: [...slotConfigs] }
 */
function computeLayout(slotConfigs, deviceType) {
  const meta = DEVICE_TYPES[deviceType];
  if (!meta) return [];
  const activeSlots = meta.slots;

  if (meta.fixedLayout) {
    return activeSlots.map((id) => {
      const cfg = slotConfigs.find((s) => s.slot_id === id) || {
        slot_id: id,
        size: 1,
        name: id === 1 ? "Top" : "Bottom",
        led_on_color: "ffffff",
        led_off_color: "0000ff",
      };
      return { startSlot: id, size: 1, slots: [cfg] };
    });
  }

  const buttons = [];
  const used = new Set();
  const sorted = [...slotConfigs].sort((a, b) => a.slot_id - b.slot_id);
  for (const cfg of sorted) {
    if (used.has(cfg.slot_id)) continue;
    if (!activeSlots.includes(cfg.slot_id)) continue;
    const btn = { startSlot: cfg.slot_id, size: cfg.size || 1, slots: [cfg] };
    used.add(cfg.slot_id);
    for (let i = 1; i < btn.size; i++) {
      used.add(cfg.slot_id + i);
    }
    buttons.push(btn);
  }

  for (const id of activeSlots) {
    if (!used.has(id)) {
      buttons.push({
        startSlot: id,
        size: 1,
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
    slot_id: id,
    size: 1,
    name: `Button ${id}`,
    behavior: "keypad",
    led_mode: "programmed",
    led_on_color: DEFAULT_COLORS.on,
    led_off_color: DEFAULT_COLORS.off,
  }));
}

/* ────────────────────── main card ────────────────────── */

class Control4Card extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._devices = [];
    this._selectedDevice = null;
    this._selectedSlotId = null;
    this._localSlots = [];
    this._dirty = false;
    this._saving = false;
    this._versionChecked = false;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._fetchDevices();
      this._checkVersion();
    }
  }

  setConfig(config) {
    this._config = config || {};
    if (this._config.ieee_address && this._devices.length > 0) {
      this._selectDevice(this._config.ieee_address);
    }
  }

  getCardSize() {
    return 6;
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  static getStubConfig() {
    return { ieee_address: "" };
  }

  async _checkVersion() {
    if (!this._hass || this._versionChecked) return;
    this._versionChecked = true;
    try {
      const resp = await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/version`,
      });
      const backendVersion = resp?.version || "0.0.0";
      if (backendVersion !== CARD_VERSION && CARD_VERSION !== "0.0.0") {
        this._showVersionMismatch = true;
        this._backendVersion = backendVersion;
        this._render();
      }
    } catch {
      // Version check failed, skip
    }
  }

  async _fetchDevices() {
    if (!this._hass) return;
    try {
      const devices = await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/devices`,
      });
      this._devices = devices || [];
      if (this._config.ieee_address) {
        this._selectDevice(this._config.ieee_address);
      }
      this._render();
    } catch (err) {
      console.error("Control4 Card: failed to fetch devices", err);
      this._render();
    }
  }

  _selectDevice(ieee) {
    const dev = this._devices.find((d) => d.ieee_address === ieee);
    this._selectedDevice = dev || null;
    this._selectedSlotId = null;
    this._dirty = false;
    if (dev) {
      const effectiveType = dev.config?.device_type_override || dev.device_type || "keypad";
      if (dev.config && dev.config.slots && dev.config.slots.length > 0) {
        this._localSlots = JSON.parse(JSON.stringify(dev.config.slots));
      } else {
        this._localSlots = defaultSlotsForType(effectiveType);
      }
    } else {
      this._localSlots = [];
    }
    this._render();
  }

  _getEffectiveType() {
    if (!this._selectedDevice) return "keypad";
    return (
      this._selectedDevice.config?.device_type_override ||
      this._selectedDevice.device_type ||
      "keypad"
    );
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

    // Remove slots that will be consumed by this button
    this._localSlots = this._localSlots.filter(
      (s) => s.slot_id < startSlot || s.slot_id >= startSlot + newSize
    );

    // Keep or create the main slot
    let mainSlot = this._localSlots.find((s) => s.slot_id === startSlot);
    if (!mainSlot) {
      mainSlot = {
        slot_id: startSlot,
        size: newSize,
        name: `Button ${startSlot}`,
        behavior: "keypad",
        led_mode: "programmed",
        led_on_color: DEFAULT_COLORS.on,
        led_off_color: DEFAULT_COLORS.off,
      };
      this._localSlots.push(mainSlot);
    }
    mainSlot.size = newSize;

    // Re-add any displaced slots
    for (const id of meta.slots) {
      if (!this._localSlots.find((s) => s.slot_id === id) && (id < startSlot || id >= startSlot + newSize)) {
        this._localSlots.push({
          slot_id: id,
          size: 1,
          name: `Button ${id}`,
          behavior: "keypad",
          led_mode: "programmed",
          led_on_color: DEFAULT_COLORS.on,
          led_off_color: DEFAULT_COLORS.off,
        });
      }
    }

    this._localSlots.sort((a, b) => a.slot_id - b.slot_id);
    this._dirty = true;
    this._selectedSlotId = startSlot;
    this._render();
  }

  async _handleTypeOverride(newType) {
    if (!this._selectedDevice || !this._hass) return;
    try {
      await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/device_config`,
        ieee_address: this._selectedDevice.ieee_address,
        device_type_override: newType || null,
      });
      this._localSlots = defaultSlotsForType(newType || this._selectedDevice.device_type || "keypad");
      this._dirty = true;
      await this._fetchDevices();
    } catch (err) {
      console.error("Failed to set device type override", err);
    }
  }

  async _handleSave() {
    if (!this._selectedDevice || !this._hass || this._saving) return;
    this._saving = true;
    this._render();
    try {
      await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/device_config`,
        ieee_address: this._selectedDevice.ieee_address,
        slots: this._localSlots,
      });
      this._dirty = false;
      await this._fetchDevices();
    } catch (err) {
      console.error("Failed to save config", err);
    } finally {
      this._saving = false;
      this._render();
    }
  }

  _handleReset() {
    if (!this._selectedDevice) return;
    const effectiveType = this._getEffectiveType();
    if (this._selectedDevice.config?.slots?.length > 0) {
      this._localSlots = JSON.parse(JSON.stringify(this._selectedDevice.config.slots));
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

    const effectiveType = this._getEffectiveType();
    const layout = computeLayout(this._localSlots, effectiveType);
    const dev = this._selectedDevice;

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        ${this._showVersionMismatch ? `
          <div class="version-mismatch">
            <span>Update available (backend: ${this._backendVersion}, card: ${CARD_VERSION})</span>
            <button id="reload-btn">Reload</button>
          </div>
        ` : ""}

        ${!dev ? this._renderDeviceSelector() : this._renderDevice(dev, effectiveType, layout)}
      </ha-card>
    `;

    this._attachListeners();
  }

  _renderDeviceSelector() {
    if (this._devices.length === 0) {
      return `
        <div class="no-device">
          <p>No Control4 devices discovered.</p>
          <p style="font-size:12px;">Make sure Z2M is running and devices are paired.</p>
        </div>
      `;
    }
    return `
      <div class="no-device">
        <p>Select a Control4 device to configure:</p>
        <select id="device-select">
          <option value="">— Choose device —</option>
          ${this._devices.map((d) => `
            <option value="${d.ieee_address}">${d.friendly_name} (${d.device_type || "unknown"})</option>
          `).join("")}
        </select>
      </div>
    `;
  }

  _renderDevice(dev, effectiveType, layout) {
    const typeMeta = DEVICE_TYPES[effectiveType] || DEVICE_TYPES.keypad;
    const selectedSlot = this._localSlots.find((s) => s.slot_id === this._selectedSlotId);

    return `
      <div class="card-header">
        <h2>${dev.friendly_name}</h2>
        <span class="device-type">${typeMeta.label}</span>
      </div>

      <div class="type-override">
        <label>Device type:</label>
        <select id="type-select">
          <option value="" ${!dev.config?.device_type_override ? "selected" : ""}>
            Auto (${dev.device_type || "unknown"})
          </option>
          ${Object.entries(DEVICE_TYPES).map(([key, val]) => `
            <option value="${key}" ${dev.config?.device_type_override === key ? "selected" : ""}>
              ${val.label}
            </option>
          `).join("")}
        </select>
      </div>

      <div class="device-layout">
        <div class="chassis" id="chassis">
          ${layout.map((btn) => this._renderChassisSlot(btn)).join("")}
        </div>

        <div class="config-panel">
          ${!typeMeta.fixedLayout ? `
            <div class="layout-toolbar">
              <span class="toolbar-label">Button size:</span>
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
              <p>Select a button on the chassis to configure it.</p>
            </div>
          `}
        </div>
      </div>

      <div class="save-bar">
        <button class="btn-reset" id="reset-btn" ${!this._dirty ? "disabled" : ""}>Reset</button>
        <button class="btn-save" id="save-btn" ${!this._dirty || this._saving ? "disabled" : ""}>
          ${this._saving ? "Saving..." : "Save Configuration"}
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
          <label>LED Colors</label>
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

    // Version reload
    const reloadBtn = root.getElementById("reload-btn");
    if (reloadBtn) reloadBtn.addEventListener("click", () => this._handleReload());

    // Device select
    const deviceSelect = root.getElementById("device-select");
    if (deviceSelect) {
      deviceSelect.addEventListener("change", (e) => {
        if (e.target.value) this._selectDevice(e.target.value);
      });
    }

    // Type override
    const typeSelect = root.getElementById("type-select");
    if (typeSelect) {
      typeSelect.addEventListener("change", (e) => this._handleTypeOverride(e.target.value));
    }

    // Chassis slot clicks
    const slots = root.querySelectorAll(".chassis-slot");
    for (const slot of slots) {
      slot.addEventListener("click", () => {
        const slotId = parseInt(slot.dataset.slot, 10);
        this._handleSlotClick(slotId);
      });
    }

    // Size buttons
    const sizeBtns = root.querySelectorAll(".size-btn");
    for (const btn of sizeBtns) {
      btn.addEventListener("click", () => {
        if (this._selectedSlotId != null) {
          this._setSlotSize(this._selectedSlotId, parseInt(btn.dataset.size, 10));
        }
      });
    }

    // Slot config inputs
    const nameInput = root.getElementById("slot-name");
    if (nameInput) {
      nameInput.addEventListener("input", (e) =>
        this._updateSlot(this._selectedSlotId, "name", e.target.value)
      );
    }
    const behaviorSelect = root.getElementById("slot-behavior");
    if (behaviorSelect) {
      behaviorSelect.addEventListener("change", (e) =>
        this._updateSlot(this._selectedSlotId, "behavior", e.target.value)
      );
    }
    const ledModeSelect = root.getElementById("slot-led-mode");
    if (ledModeSelect) {
      ledModeSelect.addEventListener("change", (e) =>
        this._updateSlot(this._selectedSlotId, "led_mode", e.target.value)
      );
    }
    const onColor = root.getElementById("slot-on-color");
    if (onColor) {
      onColor.addEventListener("input", (e) =>
        this._updateSlot(this._selectedSlotId, "led_on_color", inputColorToHex(e.target.value))
      );
    }
    const offColor = root.getElementById("slot-off-color");
    if (offColor) {
      offColor.addEventListener("input", (e) =>
        this._updateSlot(this._selectedSlotId, "led_off_color", inputColorToHex(e.target.value))
      );
    }

    // Save / Reset
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
    this._devices = [];
  }

  set hass(hass) {
    this._hass = hass;
    this._fetchDevices();
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  async _fetchDevices() {
    if (!this._hass) return;
    try {
      this._devices = await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/devices`,
      }) || [];
      this._render();
    } catch {
      this._devices = [];
      this._render();
    }
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .editor { padding: 8px 0; }
        label {
          display: block;
          font-size: 13px;
          margin-bottom: 4px;
          color: var(--secondary-text-color);
        }
        select {
          width: 100%;
          padding: 8px;
          border-radius: 6px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          color: var(--primary-text-color);
          font-size: 14px;
        }
      </style>
      <div class="editor">
        <label>Control4 Device</label>
        <select id="device-select">
          <option value="">— Auto (show selector in card) —</option>
          ${this._devices.map((d) => `
            <option value="${d.ieee_address}" ${this._config.ieee_address === d.ieee_address ? "selected" : ""}>
              ${d.friendly_name} (${d.device_type || "unknown"})
            </option>
          `).join("")}
        </select>
      </div>
    `;

    const sel = this.shadowRoot.getElementById("device-select");
    if (sel) {
      sel.addEventListener("change", (e) => {
        this._config = { ...this._config, ieee_address: e.target.value };
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
