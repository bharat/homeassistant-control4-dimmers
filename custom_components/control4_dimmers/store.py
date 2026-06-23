"""Persistent storage for Control4 device configurations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .const import (
    LOGGER,
    SNAPSHOT_STORAGE_KEY,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .models import DeviceConfig, SlotConfig

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class Control4Store:
    """Persistent storage for device slot configurations."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store."""
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry_id}"
        )
        self._devices: dict[str, DeviceConfig] = {}
        # Named snapshots of device configs, kept in a separate HA Store so
        # the device store's schema and migration path stay untouched.
        # Shape: {ieee_address: {snapshot_name: config_dict}}.
        self._snapshot_store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{SNAPSHOT_STORAGE_KEY}.{entry_id}"
        )
        self._snapshots: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def devices(self) -> dict[str, DeviceConfig]:
        """Return all stored device configs keyed by IEEE address."""
        return self._devices

    async def async_load(self) -> None:
        """Load device configs and snapshots from persistent storage."""
        await self._load_snapshots()

        stored = await self._store.async_load()
        self._devices = {}
        if not stored or not isinstance(stored, dict):
            return
        for ieee, data in stored.get("devices", {}).items():
            self._devices[ieee] = DeviceConfig.from_dict(data)

        # One-time migration: convert legacy behavior fields to action dicts
        migrated = False
        for config in self._devices.values():
            for slot in config.slots:
                if _migrate_slot(slot):
                    migrated = True
        if migrated:
            LOGGER.info("Migrated legacy behavior configs to action system")
            await self.async_save()

    async def _load_snapshots(self) -> None:
        """Load named config snapshots from persistent storage."""
        stored = await self._snapshot_store.async_load()
        self._snapshots = {}
        if not stored or not isinstance(stored, dict):
            return
        for ieee, named in stored.get("snapshots", {}).items():
            self._snapshots[ieee] = dict(named)

    async def async_save(self) -> None:
        """Persist device configs to storage."""
        payload = {
            "devices": {
                ieee: config.to_dict() for ieee, config in self._devices.items()
            }
        }
        await self._store.async_save(payload)

    def get_device(self, ieee_address: str) -> DeviceConfig | None:
        """Get a device config by IEEE address."""
        return self._devices.get(ieee_address)

    async def async_save_device(self, config: DeviceConfig) -> None:
        """Save or update a device config."""
        self._devices[config.ieee_address] = config
        await self.async_save()

    async def async_save_snapshots(self) -> None:
        """Persist named config snapshots to storage."""
        payload = {"snapshots": self._snapshots}
        await self._snapshot_store.async_save(payload)

    def get_snapshot(self, ieee_address: str, name: str) -> dict[str, Any] | None:
        """Get a named snapshot config dict for a device, or None."""
        return self._snapshots.get(ieee_address, {}).get(name)

    def list_snapshots(self, ieee_address: str) -> list[str]:
        """Return the sorted snapshot names stored for a device."""
        return sorted(self._snapshots.get(ieee_address, {}))

    async def async_save_snapshot(
        self, ieee_address: str, name: str, config_dict: dict[str, Any]
    ) -> None:
        """Save (or overwrite) a named snapshot for a device."""
        self._snapshots.setdefault(ieee_address, {})[name] = config_dict
        await self.async_save_snapshots()

    async def async_delete_snapshot(self, ieee_address: str, name: str) -> bool:
        """Delete a named snapshot. Returns True if one was removed."""
        named = self._snapshots.get(ieee_address)
        if not named or name not in named:
            return False
        del named[name]
        if not named:
            del self._snapshots[ieee_address]
        await self.async_save_snapshots()
        return True


def _migrate_slot(slot: SlotConfig) -> bool:
    """Migrate a slot to HA-native action format. Returns True if migrated."""
    a = _migrate_behavior(slot)
    b = _migrate_actions(slot)
    c = _migrate_load_actions_to_behavior(slot)
    d = _migrate_led_mode(slot)
    return a or b or c or d


_LOAD_BEHAVIORS = {"load_on", "load_off", "toggle_load"}


def _migrate_behavior(slot: SlotConfig) -> bool:
    """Convert legacy behavior field → HA-native action dicts."""
    if slot.tap_action is not None or not slot.behavior or slot.behavior == "keypad":
        return False
    # Load behaviors are firmware-native — don't convert to software actions
    if slot.behavior in _LOAD_BEHAVIORS:
        return False

    behavior = slot.behavior
    target = slot.target_entity_id

    if behavior == "control_light" and target:
        slot.tap_action = {
            "action": "light.toggle",
            "target": {"entity_id": target},
        }
        slot.led_track_entity_id = target

    slot.behavior = "keypad"
    slot.target_entity_id = None
    return True


def _migrate_actions(slot: SlotConfig) -> bool:
    """Convert intermediate action format to HA-native."""
    migrated = False
    for field in ("tap_action", "double_tap_action", "hold_action"):
        action = getattr(slot, field)
        if not action:
            continue
        action_type = action.get("action", "")
        if action_type in ("fire-event", "none"):
            setattr(slot, field, None)
            migrated = True
        elif action_type == "toggle":
            target_eid = (action.get("target") or {}).get("entity_id", "")
            domain = "light" if target_eid == "__self_load__" else "homeassistant"
            if target_eid and "." in target_eid:
                domain = target_eid.split(".")[0]
            setattr(
                slot,
                field,
                {"action": f"{domain}.toggle", "target": action.get("target", {})},
            )
            migrated = True
        elif action_type == "call-service":
            service = action.get("service", "")
            new_action = {"action": service, "target": action.get("target", {})}
            if action.get("data"):
                new_action["data"] = action["data"]
            setattr(slot, field, new_action)
            migrated = True
    return migrated


def _migrate_load_actions_to_behavior(slot: SlotConfig) -> bool:
    """
    Convert __self_load__ tap_actions back to firmware behaviors.

    Load control should be handled by firmware (fast, reliable) not
    software actions (slow round-trip). Restores the proper behavior
    field and clears the tap_action.
    """
    if not slot.tap_action:
        return False
    target = (slot.tap_action.get("target") or {}).get("entity_id", "")
    if target != "__self_load__":
        return False

    service = slot.tap_action.get("action", "")
    behavior_map = {
        "light.turn_on": "load_on",
        "light.turn_off": "load_off",
        "light.toggle": "toggle_load",
    }
    behavior = behavior_map.get(service)
    if not behavior:
        return False

    slot.behavior = behavior
    slot.tap_action = None
    slot.led_mode = "follow_load"
    return True


def _migrate_led_mode(slot: SlotConfig) -> bool:
    """Ensure LED mode is correct for the slot's behavior."""
    # Load-control buttons should always follow the load
    if slot.behavior in _LOAD_BEHAVIORS and slot.led_mode != "follow_load":
        slot.led_mode = "follow_load"
        return True
    # Programmed without tracking → fixed
    if slot.led_mode == "programmed" and not slot.led_track_entity_id:
        slot.led_mode = "fixed"
        return True
    return False
