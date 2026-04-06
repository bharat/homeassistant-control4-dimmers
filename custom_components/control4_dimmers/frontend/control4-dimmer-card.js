/**
 * Control4 Dimmers — Lovelace Card
 *
 * CARD:   Interactive control surface — press buttons, see LED states.
 * EDITOR: Full device configuration — slots, actions, LED colors, device type.
 *
 * Config: { type: "custom:control4-dimmer-card", entity: "sensor.xxx" }
 */

// Ensure HA's entity picker and service picker components are loaded (lazily loaded)
(async () => {
  if (customElements.get("ha-entity-picker") && customElements.get("ha-service-picker")) return;
  const helpers = await (window.loadCardHelpers?.() ?? Promise.resolve());
  if (helpers?.createCardElement) {
    // Creating a temporary entities card triggers HA to load its dependencies
    const el = helpers.createCardElement({type: "entities", entities: []});
    if (el?.constructor?.getConfigElement) await el.constructor.getConfigElement();
  }
})();

const DOMAIN = "control4_dimmers";
const CARD_TAG = "control4-dimmer-card";
const EDITOR_TAG = "control4-dimmer-card-editor";

/* ────────────────────── version sync ────────────────────── */

const RESOURCE_URL = (() => {
  try {
    const scripts = document.querySelectorAll(
      'script[src*="control4-dimmer-card"], link[href*="control4-dimmer-card"]'
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

const FACEPLATE_COLORS = [
  { value: "adb2bd", label: "Aluminum" },
  { value: "efe8e0", label: "Biscuit" },
  { value: "000000", label: "Black" },
  { value: "4c3d2c", label: "Brown" },
  { value: "d9caa6", label: "Ivory" },
  { value: "eee7de", label: "Light Almond" },
  { value: "333335", label: "Midnight Black" },
  { value: "ffffff", label: "White" },
];

/** Compute a button highlight color that's slightly lighter/brighter than the faceplate. */
function faceplateButtonColor(hex) {
  if (!hex) return null;
  const r = parseInt(hex.substring(0, 2), 16) || 0;
  const g = parseInt(hex.substring(2, 4), 16) || 0;
  const b = parseInt(hex.substring(4, 6), 16) || 0;
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  // Dark faceplates: lighten. Light faceplates: darken slightly.
  const factor = lum < 0.5 ? 0.15 : -0.06;
  const clamp = (v) => Math.min(255, Math.max(0, Math.round(v + factor * 255)));
  return `${clamp(r).toString(16).padStart(2, "0")}${clamp(g).toString(16).padStart(2, "0")}${clamp(b).toString(16).padStart(2, "0")}`;
}

const DEVICE_TYPES = {
  dimmer:    { label: "Dimmer",        model: "C4-APD120",   slots: [2, 5],        fixedLayout: true },
  keypaddim: { label: "Keypad Dimmer", model: "C4-KD120",    slots: [1,2,3,4,5,6], fixedLayout: false },
  keypad:    { label: "Keypad",        model: "C4-KC120277", slots: [1,2,3,4,5,6], fixedLayout: false },
};

const DOMAIN_ICONS = {
  light: "mdi:lightbulb", switch: "mdi:toggle-switch", cover: "mdi:window-shutter",
  fan: "mdi:fan", climate: "mdi:thermostat", media_player: "mdi:cast",
  scene: "mdi:palette", script: "mdi:script-text", homeassistant: "mdi:home-assistant",
  automation: "mdi:robot", input_boolean: "mdi:toggle-switch-outline",
};

/** Format an HA-native action as chip HTML matching HA's automation UI style */
function actionChipsHtml(action, hass) {
  if (!action) return null;
  const service = action.action || "";
  const [domain, svcName] = service.includes(".") ? service.split(".", 2) : ["", service];
  const domainLabel = domain.charAt(0).toUpperCase() + domain.slice(1);
  const svcLabel = svcName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const svcIcon = DOMAIN_ICONS[domain] || "mdi:cog";
  const serviceChip = `
    <span class="action-chip service-chip">
      <ha-icon icon="${svcIcon}"></ha-icon>
      ${domainLabel} '${svcLabel}'
    </span>`;

  const entityId = (action.target || {}).entity_id || "";
  let entityChip = "";
  if (entityId) {
    const entityName = entityId === "__self_load__"
      ? "This Device"
      : (hass?.states[entityId]?.attributes?.friendly_name || entityId);
    const stateObj = entityId !== "__self_load__" && hass?.states[entityId];
    const entityIcon = stateObj?.attributes?.icon || DOMAIN_ICONS[entityId.split(".")[0]] || "mdi:eye";
    entityChip = `
      <span class="action-chip entity-chip">
        <ha-icon icon="${entityIcon}"></ha-icon>
        ${entityName}
      </span>`;
  }
  return serviceChip + entityChip;
}

const LED_MODES = [
  { value: "follow_load",       label: "Follow Load",       loadOnly: true },
  { value: "follow_connection", label: "Follow Connection", loadOnly: true },
  { value: "push_release",      label: "Push/Release" },
  { value: "programmed",        label: "Programmed" },
  { value: "fixed",             label: "Fixed" },
];

const DEFAULT_COLORS = { on: "0000ff", off: "000000" };

/* ────────────────────── card styles (interactive view) ────────────────────── */

const CARD_STYLES = `
  :host {
    display: block;
  }

  ha-card {
    padding: 0;
    max-width: 16em;
    overflow: hidden;
  }

  /* ── Header ── */

  .card-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 10px;
    height: 16px;
    box-sizing: content-box;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  .card-header:active {
    opacity: 0.7;
  }
  .entity-icon {
    --mdc-icon-size: 16px;
    flex-shrink: 0;
  }
  .card-header .name {
    margin: 0;
    font-size: 13px;
    font-weight: 500;
    color: var(--primary-text-color);
    line-height: 16px;
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* ── No entity / loading ── */

  .no-entity {
    text-align: center;
    padding: 16px;
    color: var(--secondary-text-color);
    font-size: 0.8rem;
  }

  /* ── Chassis (faceplate) ── */
  /* Fixed height so dimmer (2 btn) and keypad (6 btn) match exactly. */

  .chassis {
    display: flex;
    flex-direction: column;
    border-radius: 10px;
    background: var(--secondary-background-color);
    padding: 4px;
    height: 210px;
    box-sizing: border-box;
    margin: 0 10px 10px;
  }
  .chassis-btn {
    flex: 1;
    min-height: 0;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    user-select: none;
    background: var(--c4-btn-bg, var(--card-background-color, #fff));
    border: 1px solid var(--c4-btn-border, var(--divider-color));
    margin-top: 2px;
    transition: transform 0.1s ease, background 0.15s ease;
    -webkit-tap-highlight-color: transparent;
  }
  .chassis-btn:first-child { margin-top: 0; }
  .chassis-btn:hover {
    filter: brightness(0.96);
  }
  .chassis-btn:active, .chassis-btn.pressing {
    transform: scale(0.97);
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  .chassis-btn.size-2 { flex: 2; }
  .chassis-btn.size-3 { flex: 3; }
  .chassis-btn.size-4 { flex: 4; }
  .chassis-btn.size-5 { flex: 5; }
  .chassis-btn.size-6 { flex: 6; }

  .dark-faceplate .btn-label {
    color: rgba(255,255,255,0.9);
  }

  .btn-inner {
    display: flex;
    align-items: center;
    width: 10em;
    max-width: 90%;
  }
  .btn-label {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    font-size: 12px;
    font-weight: 400;
    color: var(--primary-text-color);
  }
  .led {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-left: 6px;
    border: 1.5px solid var(--divider-color);
  }

`;

/* ────────────────────── editor styles (config view) ────────────────────── */

const EDITOR_STYLES = `
  :host {
    display: block;
    --c4-slot-height: 36px;
    --c4-chassis-width: 130px;
  }

  .editor-section {
    margin-bottom: 16px;
  }
  .editor-section:last-child { margin-bottom: 0; }

  .section-label {
    font-size: 14px;
    font-weight: 500;
    color: var(--secondary-text-color);
    margin-bottom: 8px;
    display: block;
  }

  /* ── Entity picker ── */

  .entity-picker select {
    width: 100%;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--input-fill-color, var(--secondary-background-color));
    color: var(--primary-text-color);
    font-size: 14px;
    font-family: inherit;
    outline: none;
  }
  .entity-picker select:focus { border-color: var(--primary-color); }

  .hint {
    margin-top: 6px;
    font-size: 12px;
    color: var(--secondary-text-color);
  }

  .picker-row {
    display: flex;
    gap: 12px;
  }
  .picker-row .editor-section {
    flex: 1;
    min-width: 0;
  }

  /* ── Device config box ── */

  .device-config-box {
    border: 1px solid var(--divider-color);
    border-radius: var(--ha-card-border-radius, 12px);
    padding: 14px;
    background: var(--card-background-color, #fff);
  }
  .device-config-box .box-header {
    font-size: 16px;
    font-weight: 500;
    color: var(--primary-text-color);
    margin-bottom: 14px;
  }

  /* ── Full-width selects (for top-level dropdowns) ── */

  .full-width-select {
    width: 100%;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--divider-color);
    background: var(--input-fill-color, var(--secondary-background-color));
    color: var(--primary-text-color);
    font-size: 14px;
    font-family: inherit;
    outline: none;
  }
  .full-width-select:focus { border-color: var(--primary-color); }

  /* ── Faceplate color swatches ── */

  .faceplate-swatches {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    padding: 4px 0;
  }
  .swatch {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    border: 2px solid var(--divider-color);
    cursor: pointer;
    padding: 0;
    transition: border-color 0.15s ease, transform 0.1s ease;
  }
  .swatch:hover { transform: scale(1.15); }
  .swatch.active {
    border-color: var(--primary-color);
    box-shadow: 0 0 0 2px var(--primary-color);
  }

  /* ── Chassis + config layout ── */

  .config-layout {
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }

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
    cursor: pointer;
    transition: box-shadow 0.15s ease;
    font-size: 12px;
    font-weight: 400;
    color: var(--primary-text-color);
    user-select: none;
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color);
    padding: 0 10px;
  }
  .chassis-slot:hover { box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
  .chassis-slot.selected {
    border-color: var(--primary-color);
    box-shadow: 0 0 0 1px var(--primary-color);
  }
  .chassis-slot.size-2 { height: calc(var(--c4-slot-height) * 2 + 3px); }
  .chassis-slot.size-3 { height: calc(var(--c4-slot-height) * 3 + 6px); }
  .chassis-slot .slot-label {
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
  }
  .chassis-slot .led-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-left: 6px;
  }

  /* ── Config panel ── */

  .config-panel { flex: 1; min-width: 0; }

  .slot-config {
    border-radius: 10px;
    padding: 12px 14px;
    background: var(--secondary-background-color);
  }
  .config-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
  }
  .config-row:last-child { margin-bottom: 0; }
  .config-row label {
    font-size: 13px;
    font-weight: 500;
    color: var(--secondary-text-color);
    width: 68px;
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
    font-size: 14px;
    font-family: inherit;
    outline: none;
    min-width: 0;
  }
  .config-row input[type="text"]:focus,
  .config-row select:focus { border-color: var(--primary-color); }
  .config-row ha-entity-picker { flex: 1; min-width: 0; }
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
    font-size: 12px;
    color: var(--secondary-text-color);
  }

  /* ── Inline size buttons (inside slot config) ── */

  .size-buttons {
    display: flex;
    gap: 4px;
  }
  .size-buttons button {
    padding: 5px 12px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    font-size: 13px;
    font-family: inherit;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .size-buttons button:hover {
    border-color: var(--primary-color);
    color: var(--primary-color);
  }
  .size-buttons button.active {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }

  /* ── Action rows ── */

  .btn-add-action {
    padding: 5px 12px;
    border-radius: 6px;
    border: 1px dashed var(--primary-color);
    background: transparent;
    color: var(--primary-color);
    font-size: 13px;
    font-family: inherit;
    cursor: pointer;
    font-weight: 500;
  }
  .btn-add-action:hover { background: var(--primary-color); color: var(--text-primary-color, #fff); }

  .action-box {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    padding: 6px 8px;
    border-radius: 10px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color, #fff);
    cursor: pointer;
    transition: border-color 0.15s ease;
  }
  .action-box:hover { border-color: var(--primary-color); }
  .action-chips {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
    flex-wrap: wrap;
  }
  .action-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 6px 12px;
    border-radius: 18px;
    font-size: 14px;
    font-weight: 400;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
    line-height: 20px;
  }
  .action-chip ha-icon {
    --mdc-icon-size: 18px;
    flex-shrink: 0;
    color: var(--secondary-text-color);
  }
  .service-chip {
    background: transparent;
    color: var(--primary-text-color);
  }
  .entity-chip {
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
  }

  .btn-remove-action {
    padding: 2px 6px;
    border: none;
    background: transparent;
    color: var(--secondary-text-color);
    font-size: 14px;
    cursor: pointer;
    flex-shrink: 0;
    border-radius: 4px;
  }
  .btn-remove-action:hover { color: var(--error-color, #db4437); background: var(--secondary-background-color); }

  .action-edit {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .action-edit ha-service-picker,
  .action-edit ha-entity-picker { width: 100%; }
  .action-edit-buttons {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
  }
  .action-edit-buttons button {
    padding: 4px 12px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color);
    font-size: 12px;
    font-family: inherit;
    cursor: pointer;
  }
  .action-edit-buttons .btn-action-ok {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }

  /* ── Setup prompt ── */

  .setup-prompt {
    text-align: center;
    padding: 16px;
  }
  .setup-prompt p {
    color: var(--secondary-text-color);
    font-size: 14px;
    margin: 0;
  }

  /* ── Save / Reset bar (inside device config box) ── */

  .save-error {
    margin-top: 10px;
    padding: 8px 12px;
    border-radius: 8px;
    background: var(--error-color, #db4437);
    color: #fff;
    font-size: 13px;
  }

  .save-bar {
    margin-top: 14px;
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .save-bar button {
    padding: 8px 18px;
    border-radius: 8px;
    border: none;
    font-size: 14px;
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
`;

/* ────────────────────── helpers ────────────────────── */

function hexToInputColor(hex) {
  if (!hex || hex === "000000") return "#000000";
  return "#" + hex.replace("#", "").padStart(6, "0");
}

function inputColorToHex(val) {
  return val.replace("#", "").toLowerCase();
}

function ledRingStyle(hex) {
  const h = (hex || "000000").replace("#", "");
  const r = parseInt(h.substring(0, 2), 16) || 0;
  const g = parseInt(h.substring(2, 4), 16) || 0;
  const b = parseInt(h.substring(4, 6), 16) || 0;
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  if (lum > 0.7) return "box-shadow: 0 0 0 2px rgba(0,0,0,0.4); border-color: rgba(0,0,0,0.3);";
  if (lum > 0.45) return "box-shadow: 0 0 0 1px rgba(0,0,0,0.25);";
  return "box-shadow: 0 0 4px 1px currentColor; border-color: transparent;";
}

function computeLayout(slotConfigs, deviceType) {
  const meta = DEVICE_TYPES[deviceType];
  if (!meta) return [];
  const activeSlots = meta.slots;

  if (meta.fixedLayout) {
    return activeSlots.map((id) => {
      const cfg = slotConfigs.find((s) => s.slot_id === id) || {
        slot_id: id, size: 1,
        name: id === 2 ? "Top" : "Bottom",
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
        slots: [{ slot_id: id, size: 1, name: `Button ${id}`, led_mode: "fixed", led_on_color: DEFAULT_COLORS.on, led_off_color: DEFAULT_COLORS.off }],
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
      { slot_id: 2, size: 1, name: "Top", behavior: "load_on",
        led_mode: "follow_load", led_on_color: "ffffff", led_off_color: "000000" },
      { slot_id: 5, size: 1, name: "Bottom", behavior: "load_off",
        led_mode: "follow_load", led_on_color: "000000", led_off_color: "0000ff" },
    ];
  }
  return meta.slots.map((id) => {
    const isTopLoad = deviceType === "keypaddim" && id === 1;
    return {
      slot_id: id, size: 1, name: `Button ${id}`,
      behavior: isTopLoad ? "toggle_load" : "keypad",
      led_mode: isTopLoad ? "follow_load" : "fixed",
      led_on_color: DEFAULT_COLORS.on, led_off_color: DEFAULT_COLORS.off,
    };
  });
}

function ledColor(cfg, deviceState) {
  if (cfg.led_mode === "follow_load") {
    const isOn = deviceState === "ON";
    return isOn ? (cfg.led_on_color || DEFAULT_COLORS.on) : (cfg.led_off_color || DEFAULT_COLORS.off);
  }
  return cfg.led_off_color || DEFAULT_COLORS.off;
}

/* ══════════════════════════════════════════════════════════════════════
 *  CARD — Interactive control surface
 * ══════════════════════════════════════════════════════════════════════ */

class Control4Card extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._deviceInfo = null;
    this._slots = [];
    this._eventEntities = {};
    this._versionChecked = false;
    this._lastEntityState = null;
    this._onConfigSaved = () => this._fetchDevice();
  }

  connectedCallback() {
    window.addEventListener(`${DOMAIN}-config-saved`, this._onConfigSaved);
  }

  disconnectedCallback() {
    window.removeEventListener(`${DOMAIN}-config-saved`, this._onConfigSaved);
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._checkVersion();

    const entityId = this._config.entity;
    if (entityId && hass.states[entityId]) {
      const newState = hass.states[entityId].last_updated;
      const ieee = hass.states[entityId].attributes?.ieee_address;
      if (newState !== this._lastEntityState || (!this._deviceInfo && ieee)) {
        this._lastEntityState = newState;
        if (ieee) this._fetchDevice();
      }
    }
    if (first) this._render();

    // Update LED dots for control_light buttons when tracked light state changes
    this._updateControlLightLeds(hass);
  }

  _updateControlLightLeds(hass) {
    if (!this._slots || !this.shadowRoot) return;

    // Update follow_load LEDs and header icon by checking the Z2M light state
    if (this._deviceInfo) {
      const lightId = this._findLightEntityId();
      if (lightId) {
        const lightState = hass.states[lightId];
        const isOn = lightState?.state === "on";

        // Update follow_load LEDs
        for (const slot of this._slots) {
          if (slot.led_mode !== "follow_load") continue;
          const color = isOn
            ? (slot.led_on_color || "ffffff")
            : (slot.led_off_color || "000000");
          const btn = this.shadowRoot.querySelector(
            `.chassis-btn[data-slot="${slot.slot_id}"]`
          );
          if (btn) {
            const led = btn.querySelector(".led");
            if (led) {
              led.style.background = `#${color}`;
              led.style.visibility = color === "000000" ? "hidden" : "";
            }
          }
        }

        // Update header icon to reflect light state
        const icon = this.shadowRoot.querySelector(".entity-icon");
        if (icon) {
          if (isOn) {
            const b = lightState.attributes?.brightness ?? 254;
            icon.style = `color: var(--state-light-on-color, #f9d27e); filter: brightness(${Math.round((b + 245) / 5)}%)`;
          } else {
            icon.style = "color: var(--state-icon-color, #44739e)";
          }
        }
      }
    }

    // Update programmed/tracked LEDs
    for (const slot of this._slots) {
      if (!slot.led_track_entity_id) continue;
      const targetState = hass.states[slot.led_track_entity_id];
      if (!targetState) continue;
      const isOn = targetState.state === "on";
      const color = isOn ? (slot.led_on_color || "ffffff") : (slot.led_off_color || "000000");
      const btn = this.shadowRoot.querySelector(`.chassis-btn[data-slot="${slot.slot_id}"]`);
      if (btn) {
        const led = btn.querySelector(".led");
        if (led) {
          led.style.background = `#${color}`;
          led.style.visibility = color === "000000" ? "hidden" : "";
        }
      }
    }
  }

  setConfig(config) {
    if (!config) throw new Error("No configuration provided");
    this._config = config;
    this._deviceInfo = null;
    this._slots = [];
    this._eventEntities = {};
    if (this._hass && config.entity) this._fetchDevice();
    this._render();
  }

  getCardSize() { return 3; }
  static getConfigElement() { return document.createElement(EDITOR_TAG); }
  static getStubConfig() { return { entity: "" }; }

  async _checkVersion() {
    if (!this._hass || this._versionChecked) return;
    this._versionChecked = true;
    try {
      const resp = await this._hass.connection.sendMessagePromise({ type: `${DOMAIN}/version` });
      const bv = resp?.version || "0.0.0";
      if (bv !== CARD_VERSION && CARD_VERSION !== "0.0.0") {
        this._showVersionToast(bv);
      }
    } catch { /* skip */ }
  }

  _showVersionToast(backendVersion) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: {
          message: `Control4 card update detected. Backend: ${backendVersion}, card: ${CARD_VERSION}.`,
          duration: -1,
          dismissable: true,
          action: {
            text: "Reload",
            action: () => this._handleReload(),
          },
        },
        bubbles: true,
        composed: true,
      })
    );
  }

  async _fetchDevice() {
    if (!this._hass || !this._config.entity) return;
    try {
      const info = await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/device_by_entity`,
        entity_id: this._config.entity,
      });
      const oldType = this._getEffectiveType();
      this._deviceInfo = info;
      const newType = this._getEffectiveType();
      if (info.config?.slots?.length > 0) {
        this._slots = JSON.parse(JSON.stringify(info.config.slots));
      } else {
        this._slots = defaultSlotsForType(newType);
      }
      if (info.ieee_address) {
        try {
          const evts = await this._hass.connection.sendMessagePromise({
            type: `${DOMAIN}/event_entities`,
            ieee_address: info.ieee_address,
          });
          this._eventEntities = evts || {};
        } catch { /* non-critical */ }
      }
      // Only full re-render if device type changed or first load
      if (oldType !== newType || !this.shadowRoot?.querySelector(".chassis-btn")) {
        this._render();
      }
    } catch (err) {
      console.error("Control4 Card: failed to fetch device", err);
    }
  }

  _getEffectiveType() {
    if (!this._deviceInfo) return null;
    return this._deviceInfo.config?.device_type_override
      || this._deviceInfo.device_type
      || this._deviceInfo.config?.device_type
      || "keypad";
  }

  async _pressButton(slotId) {
    if (!this._hass || !this._deviceInfo) return;
    const eventEntityId = this._eventEntities[`slot_${slotId}`];
    if (!eventEntityId) {
      console.error("No event entity for slot", slotId);
      return;
    }
    try {
      await this._hass.callService(DOMAIN, "press_button", {
        entity_id: eventEntityId,
      });
      setTimeout(() => this._fetchDevice(), 300);
    } catch (err) {
      console.error("Button press failed", err);
    }
  }

  async _handleReload() {
    const cacheNames = await caches.keys();
    await Promise.all(cacheNames.map((n) => caches.delete(n)));
    window.location.reload();
  }

  /* ── entities-card integration ── */

  _findLightEntityId() {
    if (!this._hass || !this._deviceInfo) return null;
    const friendlyName = this._deviceInfo.friendly_name;
    for (const [eid, state] of Object.entries(this._hass.states)) {
      if (eid.startsWith("light.") && state.attributes?.friendly_name === friendlyName) {
        return eid;
      }
    }
    return null;
  }

  _openMoreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      detail: { entityId },
      bubbles: true,
      composed: true,
    }));
  }

  /* ── render ── */

  _render() {
    if (!this.shadowRoot) return;

    const dev = this._deviceInfo;
    const effectiveType = this._getEffectiveType();
    const layout = computeLayout(this._slots, effectiveType);
    const devState = dev?.state;
    const brightness = dev?.brightness;
    const hasDimmer = effectiveType === "dimmer" || effectiveType === "keypaddim";

    const iconStyle = (() => {
      if (!hasDimmer) return "";
      if (devState !== "ON") return "color: var(--state-icon-color, #44739e)";
      const b = brightness != null ? brightness : 254;
      return `color: var(--state-light-on-color, #f9d27e); filter: brightness(${Math.round((b + 245) / 5)}%)`;
    })();

    this.shadowRoot.innerHTML = `
      <style>${CARD_STYLES}</style>
      <ha-card>
        ${!this._config.entity ? `
          <div class="no-entity">
            <p>No entity configured.<br>Edit this card to select a Control4 device.</p>
          </div>
        ` : !dev ? `
          <div class="no-entity"><p>Loading...</p></div>
        ` : `
          <div class="card-header">
            ${hasDimmer ? `<ha-icon class="entity-icon" icon="mdi:lightbulb" style="${iconStyle}"></ha-icon>` : ""}
            <div class="name">${dev.friendly_name}</div>
          </div>

          <div class="chassis${(() => { const c = this._config.faceplate_color; if (!c) return ""; const r = parseInt(c.substring(0,2),16)||0, g = parseInt(c.substring(2,4),16)||0, b = parseInt(c.substring(4,6),16)||0; return (0.299*r+0.587*g+0.114*b) < 128 ? " dark-faceplate" : ""; })()}" style="${(() => { const c = this._config.faceplate_color; if (!c) return ""; const bc = faceplateButtonColor(c); return `background:#${c}; --c4-btn-bg:#${bc}; --c4-btn-border:rgba(${parseInt(c,16) < 0x808080 ? "255,255,255,0.15" : "0,0,0,0.1"})`; })()}">
            ${(() => {
              // Dimmer (2 buttons) should visually match 6-slot keypad height
              const totalSlots = layout.reduce((sum, btn) => sum + btn.size, 0);
              const sizeMultiplier = totalSlots < 6 ? Math.floor(6 / totalSlots) : 1;
              return layout.map((btn) => {
                const cfg = btn.slots[0];
                const color = ledColor(cfg, devState);
                const visualSize = btn.size * sizeMultiplier;
                return `
                  <div class="chassis-btn size-${visualSize}" data-slot="${btn.startSlot}">
                    <div class="btn-inner">
                      <span class="btn-label">${cfg.name || `Button ${btn.startSlot}`}</span>
                      <div class="led" style="background:#${color}; ${ledRingStyle(color)}; ${color === "000000" ? "visibility:hidden" : ""}"></div>
                    </div>
                  </div>
                `;
              }).join("");
            })()}
          </div>
        `}
      </ha-card>
    `;

    this._attachListeners();
  }

  _attachListeners() {
    const root = this.shadowRoot;
    if (!root) return;

    const header = root.querySelector(".card-header");
    if (header) {
      header.addEventListener("click", () => {
        const lightId = this._findLightEntityId();
        if (lightId) this._openMoreInfo(lightId);
      });
    }

    const btns = root.querySelectorAll(".chassis-btn");
    for (const el of btns) {
      el.addEventListener("click", () => {
        const slotId = parseInt(el.dataset.slot, 10);
        el.classList.add("pressing");
        setTimeout(() => el.classList.remove("pressing"), 180);
        this._pressButton(slotId);
      });
    }
  }
}

/* ══════════════════════════════════════════════════════════════════════
 *  EDITOR — Device configuration UI
 * ══════════════════════════════════════════════════════════════════════ */

class Control4CardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._deviceInfo = null;
    this._localSlots = [];
    this._selectedSlotId = null;
    this._dirty = false;
    this._saving = false;
    this._lastDetectedType = null;
    this._editingAction = null; // which action field is being edited
    this._embedded = false; // true when used inside grid editor
  }

  set hass(hass) {
    const prev = this._hass;
    this._hass = hass;
    if (!prev && hass && this._config.entity) this._fetchDevice();

    // Re-render when detected_type arrives via MQTT
    const entityId = this._config.entity;
    if (entityId && hass.states[entityId]) {
      const dt = hass.states[entityId].attributes?.detected_type || null;
      if (dt !== this._lastDetectedType) {
        this._lastDetectedType = dt;
        if (prev) this._render();
      }
    }

    if (!prev) this._render();
  }

  setConfig(config) {
    this._config = config || {};
    this._deviceInfo = null;
    this._localSlots = [];
    this._selectedSlotId = null;
    this._dirty = false;
    if (this._hass && this._config.entity) this._fetchDevice();
    this._render();
  }

  _getC4Entities() {
    if (!this._hass) return [];
    return Object.keys(this._hass.states)
      .filter((eid) => {
        if (!eid.startsWith("sensor.")) return false;
        const attrs = this._hass.states[eid]?.attributes;
        return attrs?.ieee_address != null && (attrs?.device_type != null || attrs?.detected_type != null);
      })
      .map((eid) => {
        const st = this._hass.states[eid];
        const devName = st.attributes.friendly_name || eid;
        return {
          entity_id: eid,
          display_name: devName,
        };
      });
  }

  async _fetchDevice() {
    if (!this._hass || !this._config.entity) return;
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
      console.error("Editor: failed to fetch device", err);
    }
  }

  _getEffectiveType() {
    if (!this._deviceInfo) return null;
    return this._deviceInfo.config?.device_type_override
      || this._deviceInfo.device_type
      || this._deviceInfo.config?.device_type
      || "keypad";
  }

  _handleEntityChange(entityId) {
    this._config = { ...this._config, entity: entityId };
    this._deviceInfo = null;
    this._localSlots = [];
    this._selectedSlotId = null;
    this._dirty = false;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config } }));
    if (entityId) this._fetchDevice();
    else this._render();
  }

  async _handleTypeChange(newType) {
    if (!this._deviceInfo || !this._hass || !newType) return;
    try {
      await this._hass.callService(DOMAIN, "set_device_type", {
        entity_id: this._config.entity,
        device_type: newType,
      });
      // Preserve colors/config from existing slots that carry over
      const oldSlots = Object.fromEntries(this._localSlots.map((s) => [s.slot_id, s]));
      const newSlots = defaultSlotsForType(newType);
      for (const slot of newSlots) {
        const old = oldSlots[slot.slot_id];
        if (old) {
          slot.led_on_color = old.led_on_color;
          slot.led_off_color = old.led_off_color;
          slot.name = old.name;
          if (old.target_entity_id) slot.target_entity_id = old.target_entity_id;
        }
      }
      this._localSlots = newSlots;
      this._dirty = true;
      this._selectedSlotId = null;
      setTimeout(() => this._fetchDevice(), 500);
    } catch (err) {
      console.error("Failed to set device type", err);
    }
  }

  _handleSlotClick(slotId) {
    this._selectedSlotId = this._selectedSlotId === slotId ? null : slotId;
    this._editingAction = null;
    this._render();
  }

  _updateSlot(slotId, field, value) {
    const slot = this._localSlots.find((s) => s.slot_id === slotId);
    if (!slot) return;
    slot[field] = value;
    this._dirty = true;

    // For text fields, avoid a full re-render to preserve input focus.
    // Instead, surgically update any affected DOM elements.
    if (field === "name") {
      const root = this.shadowRoot;
      const displayName = value || `Button ${slotId}`;
      // Update the chassis button label on the left.
      const slotEl = root?.querySelector(`.chassis-slot[data-slot="${slotId}"]`);
      if (slotEl) {
        const label = slotEl.querySelector(".slot-label");
        if (label) label.textContent = displayName;
      }
      // Enable save/reset buttons if they were disabled.
      const saveBtn = root?.getElementById("save-btn");
      if (saveBtn) saveBtn.disabled = false;
      const resetBtn = root?.getElementById("reset-btn");
      if (resetBtn) resetBtn.disabled = false;
      return;
    }
    // For color fields, update the LED dot surgically to avoid
    // killing the native color picker dialog with a full re-render.
    if (field === "led_on_color" || field === "led_off_color") {
      const root = this.shadowRoot;
      const slotEl = root?.querySelector(`.chassis-slot[data-slot="${slotId}"]`);
      if (slotEl) {
        const dot = slotEl.querySelector(".led-dot");
        if (dot) {
          const onColor = slot.led_on_color || "0000ff";
          dot.style.background = `#${onColor}`;
        }
      }
      const saveBtn = root?.getElementById("save-btn");
      if (saveBtn) saveBtn.disabled = false;
      const resetBtn = root?.getElementById("reset-btn");
      if (resetBtn) resetBtn.disabled = false;
      return;
    }
    this._render();
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
        led_mode: "fixed",
        led_on_color: DEFAULT_COLORS.on, led_off_color: DEFAULT_COLORS.off,
        tap_action: null,
      };
      this._localSlots.push(mainSlot);
    }
    mainSlot.size = newSize;

    for (const id of meta.slots) {
      if (!this._localSlots.find((s) => s.slot_id === id) && (id < startSlot || id >= startSlot + newSize)) {
        this._localSlots.push({
          slot_id: id, size: 1, name: `Button ${id}`,
          led_mode: "fixed",
          led_on_color: DEFAULT_COLORS.on, led_off_color: DEFAULT_COLORS.off,
          tap_action: null,
        });
      }
    }

    this._localSlots.sort((a, b) => a.slot_id - b.slot_id);
    this._dirty = true;
    this._selectedSlotId = startSlot;
    this._render();
  }

  async _handleSave() {
    if (!this._deviceInfo || !this._hass || this._saving) return;

    // Validate: Programmed mode requires a tracking entity
    const invalid = this._localSlots.find(
      (s) => s.led_mode === "programmed" && !s.led_track_entity_id
    );
    if (invalid) {
      this._selectedSlotId = invalid.slot_id;
      this._saveError = `Button ${invalid.name || invalid.slot_id}: Programmed mode requires a tracking entity`;
      this._render();
      return;
    }
    this._saveError = null;

    this._saving = true;
    this._render();
    try {
      await this._hass.connection.sendMessagePromise({
        type: `${DOMAIN}/device_config`,
        ieee_address: this._deviceInfo.ieee_address,
        slots: this._localSlots,
      });
      this._dirty = false;
      await this._fetchDevice();
      window.dispatchEvent(new CustomEvent(`${DOMAIN}-config-saved`));
    } catch (err) {
      console.error("Failed to save config", err);
    } finally {
      this._saving = false;
      this._render();
    }
  }

  _handleReset() {
    if (!this._deviceInfo) return;
    if (this._deviceInfo.config?.slots?.length > 0) {
      this._localSlots = JSON.parse(JSON.stringify(this._deviceInfo.config.slots));
    } else {
      this._localSlots = defaultSlotsForType(this._getEffectiveType());
    }
    this._dirty = false;
    this._selectedSlotId = null;
    this._render();
  }

  /* ── render ── */

  _render() {
    if (!this.shadowRoot) return;

    const entities = this._getC4Entities();
    const dev = this._deviceInfo;
    const effectiveType = this._getEffectiveType();
    const layout = computeLayout(this._localSlots, effectiveType);
    const typeMeta = DEVICE_TYPES[effectiveType] || DEVICE_TYPES.keypad;
    const selectedSlot = this._localSlots.find((s) => s.slot_id === this._selectedSlotId);
    const entityAttrs = this._hass?.states[this._config.entity]?.attributes;
    const detectedType = dev?.device_type || entityAttrs?.detected_type || null;

    this.shadowRoot.innerHTML = `
      <style>${EDITOR_STYLES}</style>

      ${!this._embedded ? `
      <div class="picker-row">
        <div class="editor-section entity-picker">
          <span class="section-label">Device</span>
          <select id="entity-select">
            <option value="">-- Select --</option>
            ${entities.map((e) => `
              <option value="${e.entity_id}" ${this._config.entity === e.entity_id ? "selected" : ""}>
                ${e.display_name}
              </option>
            `).join("")}
          </select>
          ${entities.length === 0 ? `<p class="hint">No Control4 devices found. Ensure the integration is set up and Z2M is running.</p>` : ""}
        </div>
      ` : ""}

      ${dev ? `
        ${!this._embedded ? `
        <div class="editor-section">
          <span class="section-label">Type</span>
          <select class="full-width-select" id="type-select">
            ${Object.entries(DEVICE_TYPES).map(([key, val]) => {
              const isDetected = key === detectedType;
              const label = `${val.label}${isDetected ? " [detected]" : ""}`;
              return `<option value="${key}" ${effectiveType === key ? "selected" : ""}>${label}</option>`;
            }).join("")}
          </select>
        </div>
        ` : ""}
        <div class="editor-section">
          <span class="section-label">Color</span>
          <div class="faceplate-swatches">
            ${FACEPLATE_COLORS.map((c) => `
              <button class="swatch ${this._config.faceplate_color === c.value ? "active" : ""}"
                style="background:#${c.value}" data-color="${c.value}" title="${c.label}"></button>
            `).join("")}
          </div>
        </div>
      ` : ""}
      ${!this._embedded ? `</div>` : ""}

      ${dev ? `

        <!-- Button configuration -->
        <div class="device-config-box">
          <div class="box-header">Configuration</div>
          <div class="config-layout">
            <div class="chassis">
              ${layout.map((btn) => {
                const cfg = btn.slots[0];
                const isSelected = this._selectedSlotId === btn.startSlot;
                const onColor = cfg.led_on_color || DEFAULT_COLORS.on;
                return `
                  <div class="chassis-slot size-${btn.size} ${isSelected ? "selected" : ""}" data-slot="${btn.startSlot}">
                    <span class="slot-label">${cfg.name || `Button ${btn.startSlot}`}</span>
                    <div class="led-dot" style="background:#${onColor}; ${ledRingStyle(onColor)}"></div>
                  </div>
                `;
              }).join("")}
            </div>

            <div class="config-panel">
              ${selectedSlot ? this._renderSlotConfig(selectedSlot, effectiveType, typeMeta) : `
                <div class="setup-prompt">
                  <p>Select a button to configure it.</p>
                </div>
              `}
            </div>
          </div>

          <!-- Save / Reset -->
          ${this._saveError ? `<div class="save-error">${this._saveError}</div>` : ""}
          <div class="save-bar">
            <button class="btn-reset" id="reset-btn" ${!this._dirty ? "disabled" : ""}>Reset</button>
            <button class="btn-save" id="save-btn" ${!this._dirty || this._saving ? "disabled" : ""}>
              ${this._saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      ` : ""}
    `;

    this._attachEditorListeners();
  }

  _renderSlotConfig(slot, effectiveType, typeMeta) {
    const showLoadOptions = effectiveType !== "keypad";
    const showSize = typeMeta && !typeMeta.fixedLayout;
    const isLoadControl = ["load_on", "load_off", "toggle_load"].includes(slot.behavior);

    const actionConfigs = [
      { field: "tap_action", label: "Tap" },
      { field: "double_tap_action", label: "Double Tap" },
      { field: "hold_action", label: "Hold" },
    ];

    return `
      <div class="slot-config">
        ${showSize ? `
          <div class="config-row">
            <label>Slots</label>
            <div class="size-buttons">
              ${[1, 2, 3].map((size) => `
                <button class="size-btn ${slot.size === size ? "active" : ""}" data-size="${size}">
                  ${size}
                </button>
              `).join("")}
            </div>
          </div>
        ` : ""}
        <div class="config-row">
          <label>Name</label>
          <input type="text" id="slot-name" value="${slot.name || ""}" placeholder="Button ${slot.slot_id}">
        </div>

        ${showLoadOptions ? `
          <div class="config-row">
            <label>Mode</label>
            <select id="slot-mode">
              <option value="load_control" ${isLoadControl ? "selected" : ""}>Load Control</option>
              <option value="programmable" ${!isLoadControl ? "selected" : ""}>Programmable</option>
            </select>
          </div>
        ` : ""}

        ${isLoadControl ? `
          <div class="config-row">
            <label>Action</label>
            <select id="load-behavior">
              <option value="toggle_load" ${slot.behavior === "toggle_load" ? "selected" : ""}>Toggle</option>
              <option value="load_on" ${slot.behavior === "load_on" ? "selected" : ""}>Turn On</option>
              <option value="load_off" ${slot.behavior === "load_off" ? "selected" : ""}>Turn Off</option>
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
        ` : `

        ${actionConfigs.map(({ field, label }) => {
          const action = slot[field] || null;
          const chips = actionChipsHtml(action, this._hass);
          const isEditing = this._editingAction === field;
          return `
            <div class="config-row action-row">
              <label>${label}</label>
              ${!action && !isEditing ? `
                <button class="btn-add-action" data-field="${field}">+ Perform action</button>
              ` : isEditing ? `
                <div class="action-edit" data-field="${field}">
                  <ha-service-picker id="${field}-service"></ha-service-picker>
                  <ha-entity-picker id="${field}-entity" allow-custom-entity></ha-entity-picker>
                  <div class="action-edit-buttons">
                    <button class="btn-action-ok" data-field="${field}">OK</button>
                    <button class="btn-action-cancel" data-field="${field}">Cancel</button>
                  </div>
                </div>
              ` : `
                <div class="action-box" data-field="${field}">
                  <div class="action-chips">${chips}</div>
                  <button class="btn-remove-action" data-field="${field}" title="Remove">✕</button>
                </div>
              `}
            </div>
          `;
        }).join("")}

        <!-- LED Mode & Colors -->
        <div class="config-row">
          <label>LED Mode</label>
          <select id="slot-led-mode">
            ${LED_MODES.filter((m) => showLoadOptions || !m.loadOnly).map((m) => `
              <option value="${m.value}" ${slot.led_mode === m.value ? "selected" : ""}>${m.label}</option>
            `).join("")}
          </select>
        </div>
        ${slot.led_mode === "programmed" ? `
          <div class="config-row">
            <label>Track</label>
            <ha-entity-picker id="led-track-entity" allow-custom-entity></ha-entity-picker>
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
        ` : slot.led_mode === "push_release" ? `
          <div class="config-row">
            <label>Colors</label>
            <div class="color-pair">
              <span class="color-label">Pushed:</span>
              <input type="color" id="slot-on-color" value="${hexToInputColor(slot.led_on_color)}">
              <span class="color-label">Released:</span>
              <input type="color" id="slot-off-color" value="${hexToInputColor(slot.led_off_color)}">
            </div>
          </div>
        ` : slot.led_mode === "fixed" ? `
          <div class="config-row">
            <label>Color</label>
            <input type="color" id="slot-off-color" value="${hexToInputColor(slot.led_off_color)}">
          </div>
        ` : `
          <div class="config-row">
            <label>Colors</label>
            <div class="color-pair">
              <span class="color-label">On:</span>
              <input type="color" id="slot-on-color" value="${hexToInputColor(slot.led_on_color)}">
              <span class="color-label">Off:</span>
              <input type="color" id="slot-off-color" value="${hexToInputColor(slot.led_off_color)}">
            </div>
          </div>
        `}

        `}
      </div>
    `;
  }

  _attachEditorListeners() {
    const root = this.shadowRoot;
    if (!root) return;

    const entitySel = root.getElementById("entity-select");
    if (entitySel) entitySel.addEventListener("change", (e) => this._handleEntityChange(e.target.value));

    const typeSel = root.getElementById("type-select");
    if (typeSel) typeSel.addEventListener("change", (e) => this._handleTypeChange(e.target.value));

    // Faceplate color swatches
    for (const swatch of root.querySelectorAll(".swatch")) {
      swatch.addEventListener("click", () => {
        this._config = { ...this._config, faceplate_color: swatch.dataset.color };
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config } }));
        this._render();
      });
    }

    const slots = root.querySelectorAll(".chassis-slot");
    for (const slot of slots) {
      slot.addEventListener("click", () => this._handleSlotClick(parseInt(slot.dataset.slot, 10)));
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

    // Mode selector (Load Control / Programmable)
    const modeSel = root.getElementById("slot-mode");
    if (modeSel) modeSel.addEventListener("change", (e) => {
      if (e.target.value === "load_control") {
        this._updateSlot(this._selectedSlotId, "behavior", "toggle_load");
        this._updateSlot(this._selectedSlotId, "led_mode", "follow_load");
        this._updateSlot(this._selectedSlotId, "tap_action", null);
        this._updateSlot(this._selectedSlotId, "double_tap_action", null);
        this._updateSlot(this._selectedSlotId, "hold_action", null);
        this._updateSlot(this._selectedSlotId, "led_track_entity_id", null);
      } else {
        this._updateSlot(this._selectedSlotId, "behavior", "keypad");
        this._updateSlot(this._selectedSlotId, "led_mode", "fixed");
      }
      this._render();
    });

    // Load behavior selector (Turn On / Turn Off / Toggle)
    const loadBehavior = root.getElementById("load-behavior");
    if (loadBehavior) loadBehavior.addEventListener("change", (e) => {
      this._updateSlot(this._selectedSlotId, "behavior", e.target.value);
      this._render();
    });

    // "+ Perform action" buttons
    for (const btn of root.querySelectorAll(".btn-add-action")) {
      btn.addEventListener("click", () => {
        this._editingAction = btn.dataset.field;
        this._render();
      });
    }

    // Click action box to edit
    for (const box of root.querySelectorAll(".action-box")) {
      box.addEventListener("click", (e) => {
        // Don't trigger edit if clicking the remove button
        if (e.target.closest(".btn-remove-action")) return;
        this._editingAction = box.dataset.field;
        this._render();
      });
    }

    // Remove action buttons
    for (const btn of root.querySelectorAll(".btn-remove-action")) {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        this._updateSlot(this._selectedSlotId, btn.dataset.field, null);
        this._render();
      });
    }

    // Action editor: service picker, entity picker, OK/Cancel
    const actionFields = ["tap_action", "double_tap_action", "hold_action"];
    for (const field of actionFields) {
      const svcPicker = root.getElementById(`${field}-service`);
      if (svcPicker) {
        svcPicker.hass = this._hass;
        const curAction = this._localSlots.find((s) => s.slot_id === this._selectedSlotId)?.[field];
        svcPicker.value = curAction?.action || "";
      }

      const entityPicker = root.getElementById(`${field}-entity`);
      if (entityPicker) {
        entityPicker.hass = this._hass;
        const curAction = this._localSlots.find((s) => s.slot_id === this._selectedSlotId)?.[field];
        entityPicker.value = (curAction?.target || {}).entity_id || "";
      }

      const okBtn = root.querySelector(`.btn-action-ok[data-field="${field}"]`);
      if (okBtn) okBtn.addEventListener("click", () => {
        const svc = root.getElementById(`${field}-service`)?.value || "";
        const eid = root.getElementById(`${field}-entity`)?.value || "";
        if (svc) {
          const action = { action: svc };
          if (eid) action.target = { entity_id: eid };
          this._updateSlot(this._selectedSlotId, field, action);
        }
        this._editingAction = null;
        this._render();
      });

      const cancelBtn = root.querySelector(`.btn-action-cancel[data-field="${field}"]`);
      if (cancelBtn) cancelBtn.addEventListener("click", () => {
        this._editingAction = null;
        this._render();
      });
    }

    const ledModeSel = root.getElementById("slot-led-mode");
    if (ledModeSel) ledModeSel.addEventListener("change", (e) => {
      this._updateSlot(this._selectedSlotId, "led_mode", e.target.value);
      if (e.target.value !== "programmed") {
        this._updateSlot(this._selectedSlotId, "led_track_entity_id", null);
      }
      this._render();
    });

    // LED tracking entity picker (shown when mode is "track_entity")
    const ledTrackPicker = root.getElementById("led-track-entity");
    if (ledTrackPicker) {
      ledTrackPicker.hass = this._hass;
      ledTrackPicker.value = this._localSlots.find((s) => s.slot_id === this._selectedSlotId)?.led_track_entity_id || "";
      ledTrackPicker.includeDomains = ["light", "switch"];
      ledTrackPicker.addEventListener("value-changed", (e) => this._updateSlot(this._selectedSlotId, "led_track_entity_id", e.detail.value || null));
    }

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

/* ══════════════════════════════════════════════════════════════════════
 *  GRID CARD — All dimmers in one card
 * ══════════════════════════════════════════════════════════════════════ */

const GRID_TAG = "control4-dimmer-grid";
const GRID_EDITOR_TAG = "control4-dimmer-grid-editor";

class Control4DimmerGrid extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._cards = new Map(); // entity_id -> card element
  }

  set hass(hass) {
    this._hass = hass;
    this._updateCards();
  }

  setConfig(config) {
    this._config = config || {};
    // Re-apply colors to existing cards (per-device override or grid default)
    const overrides = this._config.overrides || {};
    for (const [eid, card] of this._cards) {
      const color = overrides[eid]?.faceplate_color || this._config.faceplate_color;
      card.setConfig({ entity: eid, faceplate_color: color });
    }
  }

  getCardSize() { return 6; }
  static getConfigElement() { return document.createElement(GRID_EDITOR_TAG); }
  static getStubConfig() { return {}; }

  _getC4Entities() {
    if (!this._hass) return [];
    return Object.keys(this._hass.states)
      .filter((eid) => {
        if (!eid.startsWith("sensor.")) return false;
        const attrs = this._hass.states[eid]?.attributes;
        return attrs?.ieee_address != null && (attrs?.device_type != null || attrs?.detected_type != null);
      })
      .sort();
  }

  _updateCards() {
    if (!this._hass || !this.shadowRoot) return;

    const entities = this._getC4Entities();
    const currentIds = new Set(this._cards.keys());
    const newIds = new Set(entities);

    const overrides = this._config.overrides || {};
    for (const eid of entities) {
      if (!this._cards.has(eid)) {
        const card = document.createElement(CARD_TAG);
        const color = overrides[eid]?.faceplate_color || this._config.faceplate_color;
        card.setConfig({ entity: eid, faceplate_color: color });
        this._cards.set(eid, card);
      }
    }

    for (const eid of currentIds) {
      if (!newIds.has(eid)) this._cards.delete(eid);
    }

    for (const card of this._cards.values()) {
      card.hass = this._hass;
    }

    this._render();
  }

  _render() {
    if (!this.shadowRoot) return;

    const maxCols = this._config.columns || 4;
    const grid = this.shadowRoot.getElementById("grid");
    if (grid && grid.childElementCount === this._cards.size) return;

    const title = this._config.title || "";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; overflow: hidden; }
        ha-card { padding: 0; overflow: hidden; }
        .grid-title {
          font-size: 24px; font-weight: 400;
          color: var(--primary-text-color);
          padding: 16px 16px 8px;
        }
        .grid-container {
          display: grid;
          grid-template-columns: repeat(${maxCols}, 1fr);
          gap: 8px;
          padding: 0 8px 8px;
        }
      </style>
      <ha-card>
        ${title ? `<div class="grid-title">${title}</div>` : ""}
        <div class="grid-container" id="grid"></div>
      </ha-card>
    `;

    const container = this.shadowRoot.getElementById("grid");
    for (const card of this._cards.values()) {
      container.appendChild(card);
    }
  }
}

/* ── Grid Editor ── */

class Control4DimmerGridEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._selectedTab = 0;
    this._subEditor = null; // reuse the dimmer card editor
  }

  set hass(hass) {
    this._hass = hass;
    if (this._subEditor) this._subEditor.hass = hass;
    if (!this.shadowRoot.getElementById("tabs")) this._render();
  }

  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  _getC4Entities() {
    if (!this._hass) return [];
    return Object.keys(this._hass.states)
      .filter((eid) => {
        if (!eid.startsWith("sensor.")) return false;
        const attrs = this._hass.states[eid]?.attributes;
        return attrs?.ieee_address != null && (attrs?.device_type != null || attrs?.detected_type != null);
      })
      .sort()
      .map((eid) => ({
        entity_id: eid,
        name: this._hass.states[eid]?.attributes?.friendly_name || eid,
      }));
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const entities = this._getC4Entities();
    if (this._selectedTab >= entities.length) this._selectedTab = 0;
    const selected = entities[this._selectedTab];

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .config-row {
          display: flex; align-items: center; gap: 8px; margin-bottom: 10px;
        }
        .config-row label {
          font-size: 13px; font-weight: 500;
          color: var(--secondary-text-color); width: 68px; flex-shrink: 0;
        }
        .config-row input[type="text"], .config-row input[type="number"] {
          flex: 1; padding: 6px 8px; border-radius: 6px;
          border: 1px solid var(--divider-color);
          background: var(--input-fill-color, var(--card-background-color, #fff));
          color: var(--primary-text-color); font-size: 14px; font-family: inherit;
          outline: none; min-width: 0;
        }
        .config-row input:focus { border-color: var(--primary-color); }
        .faceplate-swatches {
          display: flex; gap: 6px; flex-wrap: wrap; padding: 4px 0;
        }
        .swatch {
          width: 24px; height: 24px; border-radius: 50%;
          border: 2px solid var(--divider-color);
          cursor: pointer; padding: 0;
          transition: border-color 0.15s ease, transform 0.1s ease;
        }
        .swatch:hover { transform: scale(1.15); }
        .swatch.active {
          border-color: var(--primary-color);
          box-shadow: 0 0 0 2px var(--primary-color);
        }
        .tabs {
          display: flex; gap: 0; border-bottom: 2px solid var(--divider-color);
          margin-bottom: 12px; overflow-x: auto;
        }
        .tab {
          padding: 8px 12px; cursor: pointer;
          font-size: 13px; font-weight: 500; min-width: 32px; text-align: center;
          color: var(--secondary-text-color);
          border-bottom: 2px solid transparent;
          margin-bottom: -2px; white-space: nowrap;
          background: none; border-top: none; border-left: none; border-right: none;
          font-family: inherit;
        }
        .tab:hover { color: var(--primary-text-color); }
        .tab.active {
          color: var(--primary-color);
          border-bottom-color: var(--primary-color);
        }
        .device-name {
          font-size: 14px; font-weight: 500; color: var(--primary-text-color);
          margin-bottom: 8px;
        }
        .sub-editor-container { min-height: 200px; }
        .no-devices {
          color: var(--secondary-text-color);
          font-size: 14px; padding: 16px; text-align: center;
        }
      </style>

      <div class="config-row">
        <label>Title</label>
        <input type="text" id="grid-title" value="${this._config.title || ""}" placeholder="Control4 Dimmers">
      </div>
      <div class="config-row">
        <label>Columns</label>
        <input type="number" id="grid-columns" value="${this._config.columns || 4}" min="1" max="6" style="width:60px;flex:none">
      </div>
      <div class="config-row">
        <label>Color</label>
        <div class="faceplate-swatches">
          ${FACEPLATE_COLORS.map((c) => `
            <button class="swatch ${this._config.faceplate_color === c.value ? "active" : ""}"
              style="background:#${c.value}" data-color="${c.value}" title="${c.label}"></button>
          `).join("")}
        </div>
      </div>

      ${entities.length === 0 ? `
        <div class="no-devices">No Control4 devices found.</div>
      ` : `
        <div class="tabs" id="tabs">
          ${entities.map((e, i) => `
            <button class="tab ${i === this._selectedTab ? "active" : ""}" data-idx="${i}" title="${e.name}">
              ${i + 1}
            </button>
          `).join("")}
        </div>
        <div class="device-name">${selected?.name || ""}</div>
        <div class="sub-editor-container" id="sub-editor"></div>
      `}
    `;

    this._attachListeners();

    // Mount the sub-editor for the selected device
    if (selected) {
      const container = this.shadowRoot.getElementById("sub-editor");
      if (container) {
        this._subEditor = document.createElement(EDITOR_TAG);
        this._subEditor._embedded = true;
        this._subEditor.hass = this._hass;
        // Pass per-device color override or fall back to grid default
        const overrides = this._config.overrides || {};
        const deviceColor = overrides[selected.entity_id]?.faceplate_color || this._config.faceplate_color;
        this._subEditor.setConfig({ entity: selected.entity_id, faceplate_color: deviceColor });
        this._subEditor.addEventListener("config-changed", (e) => {
          e.stopPropagation();
          // Capture per-device color override
          const subConfig = e.detail?.config;
          if (subConfig?.faceplate_color) {
            const newOverrides = { ...this._config.overrides };
            newOverrides[selected.entity_id] = { faceplate_color: subConfig.faceplate_color };
            this._config = { ...this._config, overrides: newOverrides };
            this._fireConfigChanged();
          }
        });
        container.appendChild(this._subEditor);
      }
    }
  }

  _fireConfigChanged() {
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config } }));
  }

  _attachListeners() {
    const root = this.shadowRoot;

    const titleInput = root.getElementById("grid-title");
    if (titleInput) titleInput.addEventListener("change", (e) => {
      this._config = { ...this._config, title: e.target.value };
      this._fireConfigChanged();
    });

    const colInput = root.getElementById("grid-columns");
    if (colInput) colInput.addEventListener("change", (e) => {
      this._config = { ...this._config, columns: parseInt(e.target.value, 10) || 4 };
      this._fireConfigChanged();
    });

    for (const swatch of root.querySelectorAll(".swatch")) {
      swatch.addEventListener("click", () => {
        this._config = { ...this._config, faceplate_color: swatch.dataset.color };
        this._fireConfigChanged();
        this._render();
      });
    }

    for (const tab of root.querySelectorAll(".tab")) {
      tab.addEventListener("click", () => {
        this._selectedTab = parseInt(tab.dataset.idx, 10);
        this._render();
      });
    }
  }
}

/* ────────────────────── registration ────────────────────── */

customElements.define(CARD_TAG, Control4Card);
customElements.define(EDITOR_TAG, Control4CardEditor);
customElements.define(GRID_TAG, Control4DimmerGrid);
customElements.define(GRID_EDITOR_TAG, Control4DimmerGridEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TAG,
  name: "Control4 Dimmers",
  description: "Interactive control and configuration for Control4 dimmers and keypads.",
  preview: true,
});
window.customCards.push({
  type: GRID_TAG,
  name: "Control4 Dimmer Grid",
  description: "All Control4 dimmers and keypads in a single grid.",
  preview: false,
});

console.info(
  "%c CONTROL4-DIMMER-CARD %c loaded v" + CARD_VERSION,
  "color:#fff;background:#0a84ff;font-weight:bold;padding:2px 6px;border-radius:4px",
  ""
);
