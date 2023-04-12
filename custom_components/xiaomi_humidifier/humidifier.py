import asyncio
from enum import Enum
from functools import partial
import logging

from homeassistant.components.climate import ATTR_CURRENT_HUMIDITY, ATTR_CURRENT_TEMPERATURE
from homeassistant.components.xiaomi_miio.fan import AIRPURIFIER_SERVICE_SCHEMA
from miio import (  # pylint: disable=import-error
    Device,
    DeviceException,
)
from miio.integrations.humidifier import (
    AirHumidifier,
    AirHumidifierMjjsq,
)
from miio.integrations.humidifier.deerma.airhumidifier_mjjsq import (  # pylint: disable=import-error, import-error
    OperationMode as AirhumidifierMjjsqOperationMode,
)

import voluptuous as vol

from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_MODE,
    ATTR_TEMPERATURE, CONF_HOST,
    CONF_NAME,
    CONF_TOKEN,
)
from homeassistant.components.humidifier import (
    ATTR_HUMIDITY, DEVICE_CLASS_HUMIDIFIER, HumidifierEntity, PLATFORM_SCHEMA, SUPPORT_MODES,
)

from homeassistant.exceptions import PlatformNotReady
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Xiaomi Humidifier"
DEFAULT_RETRIES = 20
DATA_KEY = "humidifier.xiaomi_humidifier"
DOMAIN = "xiaomi_humidifier"
SUCCESS = ["ok"]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_TOKEN): vol.All(cv.string, vol.Length(min=32, max=32)),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)

ATTR_MODEL = "model"
ATTR_NO_WATER = "no_water"
ATTR_WATER_TANK_DETACHED = "water_tank_detached"
ATTR_BUZZER = "buzzer"
ATTR_LED = "led"
ATTR_TARGET_HUMIDITY = "target_humidity"
ATTR_HUMIDITY_SENSOR = "humidity_sensor"
AVAILABLE_ATTRIBUTES_AIRHUMIDIFIER = {
    ATTR_CURRENT_TEMPERATURE: "temperature",
    ATTR_CURRENT_HUMIDITY: "humidity",
    ATTR_MODE: "mode",
    ATTR_BUZZER: "buzzer",
    ATTR_LED: "led",
    ATTR_NO_WATER: "no_water",
    ATTR_WATER_TANK_DETACHED: "water_tank_detached",
    ATTR_TARGET_HUMIDITY: "target_humidity",
}
SERVICE_TO_METHOD = {
    "set_buzzer_on": {"method": "async_set_buzzer_on"},
    "set_buzzer_off": {"method": "async_set_buzzer_off"},
    "set_led_on": {"method": "async_set_led_on"},
    "set_led_off": {"method": "async_set_led_off"},
}


class XiaomiAirHumidifier(HumidifierEntity):
    def __init__(self, name, device, unique_id):
        self._name = name
        self._device = device
        self._unique_id = unique_id
        self._available = False
        self._state = None
        self._skip_update = False
        self._available_attributes = AVAILABLE_ATTRIBUTES_AIRHUMIDIFIER
        self._mode_list = [mode.name for mode in AirhumidifierMjjsqOperationMode]
        self._state_attrs = {}
        self._state_attrs = {attribute: None for attribute in self._available_attributes}

    @property
    def supported_features(self):
        return SUPPORT_MODES

    @property
    def should_poll(self):
        """Poll the device."""
        return True

    @property
    def unique_id(self):
        """Return an unique ID."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of the device if any."""
        return self._name

    @property
    def available(self):
        """Return true when state is known."""
        return self._available

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        return self._state_attrs

    @property
    def device_class(self):
        return DEVICE_CLASS_HUMIDIFIER

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._state

    @property
    def min_humidity(self) -> int:
        return 40

    @property
    def max_humidity(self) -> int:
        return 70

    @property
    def target_humidity(self):
        return self._state_attrs[ATTR_TARGET_HUMIDITY]

    @staticmethod
    def _extract_value_from_attribute(state, attribute):
        value = getattr(state, attribute)
        if isinstance(value, Enum):
            return value.value

        return value

    async def _try_command(self, mask_error, func, *args, **kwargs):
        """Call a miio device command handling error messages."""
        try:
            result = await self.hass.async_add_executor_job(
                partial(func, *args, **kwargs)
            )

            _LOGGER.debug("Response received from miio device: %s", result)

            return result == SUCCESS
        except DeviceException as exc:
            _LOGGER.error(mask_error, exc)
            self._available = False
            return False

    async def async_turn_on(self, mode: str = None, **kwargs) -> None:
        """Turn the device on."""
        if mode:
            # If operation mode was set the device must not be turned on.
            result = await self.async_set_mode(mode)
        else:
            result = await self._try_command(
                "Turning the miio device on failed.", self._device.on
            )

        if result:
            self._state = True
            self._skip_update = True

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the device off."""
        result = await self._try_command(
            "Turning the miio device off failed.", self._device.off
        )

        if result:
            self._state = False
            self._skip_update = True

    async def async_set_buzzer_on(self):
        """Turn the buzzer on."""
        await self._try_command(
            "Turning the buzzer of the miio device on failed.",
            self._device.set_buzzer,
            True,
        )

    async def async_set_buzzer_off(self):
        """Turn the buzzer off."""
        await self._try_command(
            "Turning the buzzer of the miio device off failed.",
            self._device.set_buzzer,
            False,
        )

    @property
    def mode(self):
        """Return the current speed."""
        if self._state:
            return AirhumidifierMjjsqOperationMode(self._state_attrs[ATTR_MODE]).name

        return None

    async def async_set_mode(self, mode: str) -> None:
        """Set the speed of the fan."""
        _LOGGER.debug("Setting the operation mode to: %s", mode)

        await self._try_command(
            "Setting operation mode of the miio device failed.",
            self._device.set_mode,
            AirhumidifierMjjsqOperationMode[mode.title()],
        )

    async def async_update(self):
        """Fetch state from the device."""
        # On state change the device doesn't provide the new state immediately.
        if self._skip_update:
            self._skip_update = False
            return

        try:
            state = await self.hass.async_add_executor_job(self._device.status)
            _LOGGER.debug("Got new state: %s", state)

            self._available = True
            self._state = state.is_on
            self._state_attrs.update(
                {
                    key: self._extract_value_from_attribute(state, value)
                    for key, value in self._available_attributes.items()
                }
            )

        except DeviceException as ex:
            self._available = False
            _LOGGER.error("Got exception while fetching the state: %s", ex)

    @property
    def available_modes(self):
        return self._mode_list

    async def async_set_led_on(self):
        """Turn the led on."""
        await self._try_command(
            "Turning the led of the miio device off failed.", self._device.set_led, True
        )

    async def async_set_led_off(self):
        """Turn the led off."""
        await self._try_command(
            "Turning the led of the miio device off failed.",
            self._device.set_led,
            False,
        )

    async def async_set_humidity(self, humidity: int) -> None:
        """Set the target humidity."""
        await self._try_command(
            "Setting the target humidity of the miio device failed.",
            self._device.set_target_humidity,
            humidity,
        )


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the miio fan device from config."""
    if DATA_KEY not in hass.data:
        hass.data[DATA_KEY] = {}

    host = config[CONF_HOST]
    token = config[CONF_TOKEN]
    name = config[CONF_NAME]

    _LOGGER.info("Initializing with host %s (token %s...)", host, token[:5])
    unique_id = None

    air_humidifier = AirHumidifierMjjsq(host, token)
    device = XiaomiAirHumidifier(name, air_humidifier, unique_id)
    hass.data[DATA_KEY][host] = device
    async_add_entities([device], update_before_add=True)

    async def async_service_handler(service):
        """Map services to methods on XiaomiAirPurifier."""
        method = SERVICE_TO_METHOD.get(service.service)
        params = {
            key: value for key, value in service.data.items() if key != ATTR_ENTITY_ID
        }
        entity_ids = service.data.get(ATTR_ENTITY_ID)
        if entity_ids:
            devices = [
                device
                for device in hass.data[DATA_KEY].values()
                if device.entity_id in entity_ids
            ]
        else:
            devices = hass.data[DATA_KEY].values()

        update_tasks = []
        for device in devices:
            if not hasattr(device, method["method"]):
                continue
            await getattr(device, method["method"])(**params)
            update_tasks.append(device.async_update_ha_state(True))

        if update_tasks:
            await asyncio.wait(update_tasks)

    for air_purifier_service in SERVICE_TO_METHOD:
        schema = SERVICE_TO_METHOD[air_purifier_service].get(
            "schema", AIRPURIFIER_SERVICE_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, air_purifier_service, async_service_handler, schema=schema
        )


