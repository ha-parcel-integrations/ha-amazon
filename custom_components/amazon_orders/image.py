"""Image platform for Amazon: the delivery photos of delivered parcels."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import AmazonConfigEntry
from .account.coordinator import AmazonCoordinator, photo_unique_id
from .device import ATTRIBUTION, build_device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmazonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add one image entity per delivery photo and keep the set in sync."""
    coordinator = entry.runtime_data.coordinator
    # The platform's own add is awaitable, unlike the callback: the coordinator
    # holds the delivered event back until the entity exists.
    platform = async_get_current_platform()
    known: set[str] = set()
    registry = er.async_get(hass)

    async def _sync() -> None:
        new = set(coordinator.photos) - known
        if new:
            known.update(new)
            await platform.async_add_entities(
                [
                    AmazonDeliveryPhoto(hass, coordinator, entry, barcode)
                    for barcode in sorted(new)
                ]
            )
        # Until every shipment was read again, a photo missing from the list
        # after a restart may just not have been reached yet.
        if not coordinator.listing_complete:
            return
        current = {
            photo_unique_id(entry.entry_id, barcode) for barcode in coordinator.photos
        }
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == "image" and entity.unique_id not in current:
                # Removed via the registry, not self-removal, to avoid ghost entities.
                registry.async_remove(entity.entity_id)
        known.intersection_update(coordinator.photos)

    await _sync()
    entry.async_on_unload(coordinator.async_set_photo_entity_sync(_sync))


class AmazonDeliveryPhoto(CoordinatorEntity[AmazonCoordinator], ImageEntity):
    """The photo Amazon's own fleet took of one delivered parcel."""

    _attr_has_entity_name = True
    _attr_translation_key = "delivery_photo"
    _attr_content_type = "image/jpeg"
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: AmazonCoordinator,
        entry: ConfigEntry,
        barcode: str,
    ) -> None:
        """Initialise the image entity."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._barcode = barcode
        self._attr_unique_id = photo_unique_id(entry.entry_id, barcode)
        self._attr_translation_placeholders = {"barcode": barcode}
        self._attr_device_info = build_device_info(entry)
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def available(self) -> bool:
        """Return whether the parcel still has a photo."""
        return super().available and self._barcode in self.coordinator.photos

    async def async_image(self) -> bytes | None:
        """Return the photo for Home Assistant's authenticated image proxy."""
        photo = await self.coordinator.async_load_photo(self._barcode)
        if photo is None or photo.content is None:
            return None
        if photo.content_type and photo.content_type.startswith("image/"):
            self._attr_content_type = photo.content_type
        return photo.content
