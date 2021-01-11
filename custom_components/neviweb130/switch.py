"""
Need to be changed
Support for Neviweb switch connected via GT130 ZigBee.
model 2506 = load controller device, RM3250ZB, 50A
model 2610 = wall outlet, SP2610ZB
model 2600 = portable plug, SP2600ZB
model xxx = VA4201WZ, sedna valve 1 inch
model xxx = VA4200WZ, sedna valve 3/4 inch
For more details about this platform, please refer to the documentation at  
https://www.sinopetech.com/en/support/#api
"""
import logging

import voluptuous as vol
import time

import custom_components.neviweb130 as neviweb130
from . import (SCAN_INTERVAL)
from homeassistant.components.switch import (
    SwitchEntity,
    ATTR_TODAY_ENERGY_KWH,
    ATTR_CURRENT_POWER_W,
)

from homeassistant.const import (
    ATTR_ENTITY_ID,
    DEVICE_CLASS_BATTERY,
    DEVICE_CLASS_TEMPERATURE,
    DEVICE_CLASS_POWER,
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
)

from homeassistant.helpers import (
    config_validation as cv,
    discovery,
    service,
    entity_platform,
    entity_component,
    entity_registry,
    device_registry,
)

from homeassistant.helpers.typing import HomeAssistantType

from datetime import timedelta
from homeassistant.helpers.event import track_time_interval
from homeassistant.helpers.icon import icon_for_battery_level
from .const import (
    DOMAIN,
    ATTR_ONOFF,
    ATTR_WATTAGE_INSTANT,
    ATTR_WATTAGE,
    ATTR_VALVE_STATUS,
    ATTR_BATTERY_VOLTAGE,
    ATTR_TIMER,
    ATTR_KEYPAD,
    ATTR_DRSTATUS,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_OFF,
    STATE_VALVE_STATUS,
    STATE_KEYPAD_STATUS,
    SERVICE_SET_KEYPAD_LOCK,
    SERVICE_SET_TIMER,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'neviweb130 switch'

UPDATE_ATTRIBUTES = [ATTR_ONOFF]

IMPLEMENTED_VALVE_MODEL = []
IMPLEMENTED_WALL_DEVICES = [2600, 2610]
IMPLEMENTED_LOAD_DEVICES = [2506]
IMPLEMENTED_DEVICE_MODEL = IMPLEMENTED_LOAD_DEVICES + IMPLEMENTED_WALL_DEVICES + IMPLEMENTED_VALVE_MODEL

SET_KEYPAD_LOCK_SCHEMA = vol.Schema(
    {
         vol.Required(ATTR_ENTITY_ID): cv.entity_id,
         vol.Required(ATTR_KEYPAD): cv.string,
    }
)

SET_TIMER_SCHEMA = vol.Schema(
    {
         vol.Required(ATTR_ENTITY_ID): cv.entity_id,
         vol.Required(ATTR_TIMER): vol.All(
             vol.Coerce(int), vol.Range(min=0, max=255)
         ),
    }
)

async def async_setup_platform(
    hass,
    config,
    async_add_entities,
    discovery_info=None,
):
    """Set up the Neviweb130 switch."""
    data = hass.data[DOMAIN]

    entities = []
    for device_info in data.neviweb130_client.gateway_data:
        if "signature" in device_info and \
            "model" in device_info["signature"] and \
            device_info["signature"]["model"] in IMPLEMENTED_DEVICE_MODEL:
            device_name = '{} {}'.format(DEFAULT_NAME, device_info["name"])
            entities.append(Neviweb130Switch(data, device_info, device_name))

    async_add_entities(entities, True)

    def set_keypad_lock_service(service):
        """ lock/unlock keypad device"""
        entity_id = service.data[ATTR_ENTITY_ID]
        value = {}
        for switch in entities:
            if switch.entity_id == entity_id:
                value = {"id": switch.unique_id, "lock": service.data[ATTR_KEYPAD]}
                switch.set_keypad_lock(value)
                switch.schedule_update_ha_state(True)
                break

    def set_timer_service(service):
        """ set timer for switch device"""
        entity_id = service.data[ATTR_ENTITY_ID]
        value = {}
        for switch in entities:
            if switch.entity_id == entity_id:
                value = {"id": switch.unique_id, "time": service.data[ATTR_TIMER]}
                switch.set_timer(value)
                switch.schedule_update_ha_state(True)
                break

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_KEYPAD_LOCK,
        set_keypad_lock_service,
        schema=SET_KEYPAD_LOCK_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_TIMER,
        set_timer_service,
        schema=SET_TIMER_SCHEMA,
    )

def voltage_to_percentage(voltage):
    """Convert voltage level from absolute 0..3.25 to percentage."""
    return int((voltage * 100.0) / 3.25)

class Neviweb130Switch(SwitchEntity):
    """Implementation of a Neviweb switch."""

    def __init__(self, data, device_info, name):
        """Initialize."""
        self._name = name
        self._client = data.neviweb130_client
        self._id = device_info["id"]
        self._current_power_w = None
        self._today_energy_kwh = None
        self._onOff = None
        self._is_wall = device_info["signature"]["model"] in \
            IMPLEMENTED_WALL_DEVICES
        self._is_load = device_info["signature"]["model"] in \
            IMPLEMENTED_LOAD_DEVICES
        self._is_valve = device_info["signature"]["model"] in \
            IMPLEMENTED_VALVE_MODEL
        self._valve_status = None
        self._cur_temp = None
        self._battery_voltage = None
        self._timer = 0
        self._keypad = None
        self._drstatus_active = None
        self._drstatus_out = None
        self._drstatus_onoff = None
        _LOGGER.debug("Setting up %s: %s", self._name, device_info)

    def update(self):
        if self._is_load:
            LOAD_ATTRIBUTE = [ATTR_WATTAGE_INSTANT, ATTR_WATTAGE, ATTR_TIMER, ATTR_KEYPAD, ATTR_DRSTATUS]
        elif self._is_valve:
            LOAD_ATTRIBUTE = [ATTR_VALVE_STATUS, ATTR_ROOM_TEMPERATURE, ATTR_BATTERY_VOLTAGE]
        else:
            LOAD_ATTRIBUTE = [ATTR_WATTAGE_INSTANT]
        """Get the latest data from Neviweb and update the state."""
        start = time.time()
        device_data = self._client.get_device_attributes(self._id,
            UPDATE_ATTRIBUTES + LOAD_ATTRIBUTE)
        device_daily_stats = self._client.get_device_daily_stats(self._id)
        end = time.time()
        elapsed = round(end - start, 3)
        _LOGGER.debug("Updating %s (%s sec): %s",
            self._name, elapsed, device_data)
        if "error" not in device_data:
            if "errorCode" not in device_data:
                if self._is_valve:
                    self._valve_status = STATE_VALVE_STATUS if \
                        device_data[ATTR_VALVE_STATUS] == STATE_VALVE_STATUS else "closed"
                    self._cur_temp = device_data[ATTR_ROOM_TEMPERATURE]
                    self._battery_voltage = device_data[ATTR_BATTERY_VOLTAGE]
                elif self._is_load: #for is_load
                    self._current_power_w = device_data[ATTR_WATTAGE_INSTANT]
                    self._wattage = device_data[ATTR_WATTAGE]
                    self._keypad = STATE_KEYPAD_STATUS if \
                        device_data[ATTR_KEYPAD] == STATE_KEYPAD_STATUS else "locked" 
                    self._timer = device_data[ATTR_TIMER]
                    self._onOff = device_data[ATTR_ONOFF]
                    self._drstatus_active = device_data[ATTR_DRSTATUS]["drActive"]
                    self._drstatus_out = device_data[ATTR_DRSTATUS]["optOut"]
                    self._drstatus_onoff = device_data[ATTR_DRSTATUS]["onOff"]
                else: #for is_wall
                    self._current_power_w = device_data[ATTR_WATTAGE_INSTANT]
                    self._onOff = device_data[ATTR_ONOFF]
#                self._today_energy_kwh = device_daily_stats[0] / 1000
                return
            _LOGGER.warning("Error in reading device %s: (%s)", self._name, device_data)
            return
        _LOGGER.warning("Cannot update %s: %s", self._name, device_data)     

    @property
    def unique_id(self):
        """Return unique ID based on Neviweb device ID."""
        return self._id

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def device_class(self):
        """Return the device class of this entity."""
        return DEVICE_CLASS_POWER

    @property  
    def is_on(self):
        """Return current operation i.e. ON, OFF """
        return self._onOff != MODE_OFF

    def turn_on(self, **kwargs):
        """Turn the device on."""
        self._client.set_onOff(self._id, "on")

    def turn_off(self, **kwargs):
        """Turn the device off."""
        self._client.set_onOff(self._id, "off")

    @property  
    def valve_status(self):
        """Return current valve status, open or closed"""
        return self._valve_status != None

    @property  
    def keypad_status(self):
        """Return current keypad status, unlocked or locked"""
        return self._keypad_status != None

    @property
    def current_temperature(self):
        """Return the current valve temperature."""
        return self._cur_temp

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        data = {}
        if self._is_load:
            data = {'onOff': self._onOff,
                   'Wattage': self._wattage,
                   'Keypad': self._keypad,
                   'Timer': self._timer,
                   'drstatus_active': self._drstatus_active,
                   'drstatus_optOut': self._drstatus_out,
                   'drstatus_onoff': self._drstatus_onoff}
        elif self._is_valve:
            data = {'Valve_status': self._valve_status,
                   'Temperature': self._cur_temp,
                   'Battery': voltage_to_percentage(self._battery_voltage)}
        else:
            data = {'onOff': self._onOff,
                   'Wattage': self._current_power_w}
        data.update({'id': self._id})
        return data

    @property
    def battery_voltage(self):
        """Return the current battery voltage of the valve in %."""
        return voltage_to_percentage(self._battery_voltage)

    @property
    def current_power_w(self):
        """Return the current power usage in W."""
        return self._current_power_w

    @property
    def today_energy_kwh(self):
        """Return the today total energy usage in kWh."""
        return self._today_energy_kwh

    @property
    def is_standby(self):
        """Return true if device is in standby."""
        return self._current_power_w == 0

    def set_keypad_lock(self, value):
        """Lock or unlock device's keypad, lock = locked, unlock = unlocked"""
        lock = value["lock"]
        entity = value["id"]
        key = "off"
        if lock == "locked":
            lock_name = "Locked"
        else:
            lock_name = "Unlocked"
        self._client.set_keypad_lock(
            entity, lock, key)
        self._keypad = lock_name

    def set_timer(self, value):
        """Set device timer, 0 = off, 1 to 255 = timer length"""
        time = value["time"]
        entity = value["id"]
        self._client.set_timer(
            entity, time)
        self._timer = time
