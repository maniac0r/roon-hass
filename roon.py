from __future__ import unicode_literals
import voluptuous as vol
import logging
import asyncio
import json
import aiohttp
import async_timeout
import time
import os.path

"""
Support to interface with the Roon API.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.roon/
"""

from homeassistant.components.media_player import (
    ATTR_MEDIA_ENQUEUE, SUPPORT_PLAY_MEDIA, SUPPORT_SELECT_SOURCE, SUPPORT_STOP, SUPPORT_SHUFFLE_SET,
    MEDIA_TYPE_MUSIC, SUPPORT_NEXT_TRACK, SUPPORT_PAUSE, PLATFORM_SCHEMA,
    SUPPORT_PREVIOUS_TRACK, SUPPORT_SEEK, SUPPORT_TURN_OFF, SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_SET, SUPPORT_PLAY, MediaPlayerDevice)
from homeassistant.const import (
    STATE_IDLE, STATE_OFF, STATE_PAUSED, STATE_PLAYING,
    CONF_HOST, CONF_PORT, CONF_SSL, CONF_API_KEY, DEVICE_DEFAULT_NAME,
    EVENT_HOMEASSISTANT_START, EVENT_HOMEASSISTANT_STOP)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.dt import utcnow
import homeassistant.helpers.config_validation as cv
from homeassistant.core import callback
from homeassistant.helpers import event
from homeassistant.helpers.discovery import load_platform

try:
    ensure_future = asyncio.ensure_future
except AttributeError:
    # Python 3.4.3 and earlier has this as async
    ensure_future = asyncio.async


_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ['roonapi>=0.0.15']

TOKEN_FILE = '.roontoken'

TIMEOUT = 10
UPDATE_PLAYLISTS_INTERVAL = 360
CONF_CUSTOM_PLAY_ACTION = 'custom_play_action'
CONF_SOURCE_CONTROLS = 'source_controls'
CONF_VOLUME_CONTROLS = 'volume_controls'

SUPPORT_ROON = SUPPORT_PAUSE | SUPPORT_VOLUME_SET | SUPPORT_STOP | \
    SUPPORT_PREVIOUS_TRACK | SUPPORT_NEXT_TRACK | SUPPORT_SHUFFLE_SET | \
    SUPPORT_SEEK | SUPPORT_TURN_ON | SUPPORT_TURN_OFF | SUPPORT_VOLUME_MUTE | \
    SUPPORT_PLAY | SUPPORT_PLAY_MEDIA


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_HOST): cv.string,
    vol.Optional(CONF_CUSTOM_PLAY_ACTION): cv.string,
    vol.Optional(CONF_SOURCE_CONTROLS): cv.entity_ids,
    vol.Optional(CONF_VOLUME_CONTROLS): cv.entity_ids,
})


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the Roon platform."""
    host = config.get(CONF_HOST)
    custom_play_action = config.get(CONF_CUSTOM_PLAY_ACTION)
    from roon import RoonApi
    appinfo = {
            "extension_id": "home_assistant",
            "display_name": "Home Assistant",
            "display_version": "1.0.0",
            "publisher": "marcelveldt",
            "email": "marcelveldt@users.noreply.github.com",
            "website": "https://github.com/marcelveldt/roon-hass"
        }
    token = None
    token_file = hass.config.path(TOKEN_FILE)
    _LOGGER.debug("token file location: %s" % token_file)
    if os.path.isfile(token_file):
        with open(token_file) as f:
            token = f.read()
    if not token:
        _LOGGER.warning("App not yet registered within Roon. You should allow it in Roon's settings.")

    source_controls = config.get(CONF_SOURCE_CONTROLS)
    volume_controls = config.get(CONF_VOLUME_CONTROLS)
    registed_source_controls = []
    registered_volume_controls = []

    roonapi = RoonApi(appinfo, token, host, blocking_init=False)
    roon = RoonServer(hass, roonapi, async_add_devices, custom_play_action)

    def roon_source_control_callback(control_key, new_state):
        entity_obj = hass.states.get(control_key)
        if "media_player" in control_key and "roon" in entity_obj.attributes.get("source_list", []):
            if new_state == "standby" and entity_obj.attributes.get("source", "") == "roon":
                hass.services.call('media_player', "turn_off", {"entity_id": control_key})
            elif new_state == "convenience_switch":
                hass.services.call('media_player', "select_source", {"entity_id": control_key, "source": "roon"})
        else:
            # just use on/off control
            svc = "turn_off" if new_state == "standby" else "turn_on"
            hass.services.call('homeassistant', svc, {"entity_id": control_key})

    def roon_volume_control_callback(control_key, event, data):
        if event == "set_mute":
            hass.services.call('media_player', "volume_mute", {"entity_id": control_key, "is_volume_muted": data})
        elif event == "set_volume":
            hass_vol = data/100
            hass.services.call('media_player', "volume_set", {"entity_id": control_key, "volume_level": hass_vol})


    @asyncio.coroutine
    def hass_state_event(entity_id, old_state, new_state ):
        entity_obj = hass.states.get(entity_id)
        if entity_id in source_controls:
            old_state = old_state.state if old_state else None
            if "media_player" in entity_id and "roon" in entity_obj.attributes.get("source_list", []):
                if new_state.attributes.get("source", "") == "roon":
                    src_state = "selected"
                elif new_state.state == "off":
                    src_state = "standby"
                else:
                    src_state = "deselected"
            else:
                src_state = "selected" if new_state.state == "on" else "standby"
            if not entity_id in registed_source_controls:
                # register as source control
                registed_source_controls.append(entity_id)
                roonapi.register_source_control(entity_id, entity_obj.attributes.get("friendly_name"), roon_source_control_callback, src_state)
            else:
                new_state = new_state.state if new_state else None
                roonapi.update_source_control(entity_id, src_state)
        if entity_id in volume_controls:
            cur_vol = entity_obj.attributes.get("volume_level", 0) * 100
            cur_mute = entity_obj.attributes.get("is_volume_muted", False)
            if not entity_id in registered_volume_controls:
                # register as volume control
                registered_volume_controls.append(entity_id)
                roonapi.register_volume_control(entity_id, entity_obj.attributes.get("friendly_name"), roon_volume_control_callback, cur_vol, is_muted=cur_mute)
            else:
                roonapi.update_volume_control(entity_id, cur_vol, cur_mute)


    @asyncio.coroutine
    def stop_roon(event):
        """Stop Roon connection."""
        _LOGGER.debug("stop requested")
        with open(token_file, 'w') as f:
            f.write(roonapi.token)
        roonapi.stop()
        roon.stop_roon()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_roon)
    if source_controls or volume_controls:
        entity_ids = source_controls + volume_controls
        event.async_track_state_change(hass, entity_ids, hass_state_event)
        for entity_id in entity_ids:
            entity_obj = hass.states.get(entity_id)
            if entity_obj:
                asyncio.run_coroutine_threadsafe(hass_state_event(entity_id, entity_obj, entity_obj), hass.loop)

    roon.start_roon()


class RoonDevice(MediaPlayerDevice):
    """Representation of an Roon device."""

    def __init__(self, server, player_data):
        """Initialize Roon device object."""
        self._server = server
        self._available = True
        self._last_position_update = None
        self._supports_standby = False
        self._state = STATE_IDLE
        self._fake_power_off = False
        self.update_data(player_data)
        

    @property
    def hidden(self):
        """Return True if entity should be hidden from UI."""
        return not self._available

    def set_hidden(self, value):
        """Set hidden property."""
        self._available = not value

    @property
    def available(self):
        """Return True if entity is available."""
        return self._available

    def set_available(self, value):
        """Set available property."""
        self._available = value

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_ROON

    @asyncio.coroutine
    def async_update(self):
        """Retrieve the current state of the player."""
        self.update_data(self.player_data)

    def update_data(self, player_data=None):
        """ Update session object. """
        if player_data:
            self.player_data = player_data
        self._available = self.player_data["is_available"]
        # determine player state
        self.update_state()
        if self.state == STATE_PLAYING:
            self._last_position_update = utcnow()
        
    def update_state(self):
        ''' update the power state and player state '''
        if not self.available:
            self._state = STATE_OFF
        else:
            cur_state = self._state
            new_state = ""
            # power state from source control (if supported)
            if 'source_controls' in self.player_data:
                for source in self.player_data["source_controls"]:
                    if source["supports_standby"]:
                        if not source["status"] == "indeterminate":
                            self._supports_standby = True
                            if source["status"] in ["standby", "deselected"]:
                                new_state = STATE_OFF
                            break
            # determine player state
            if not new_state:
                if self.player_data['state'] == 'playing':
                    new_state = STATE_PLAYING
                elif self.player_data['state'] == 'loading':
                    new_state = STATE_PLAYING
                elif self.player_data['state'] == 'stopped':
                    new_state = STATE_IDLE
                elif self.player_data['state'] == 'paused':
                    new_state = STATE_PAUSED
                else:
                    new_state = STATE_IDLE
            # treat idle as off on devices that do not support standby feature
            if new_state == STATE_IDLE and not self.supports_standby:
                new_state = STATE_OFF
            self._state = new_state

    @asyncio.coroutine
    def async_added_to_hass(self):
        """Register callback."""
        _LOGGER.info("New Roon Device %s initialized with ID: %s" % (self.entity_id, self.unique_id))
        self._server.add_update_callback(
            self.async_update_callback, self.unique_id)

    @callback
    def async_update_callback(self, msg):
        """Handle device updates."""
        self.async_schedule_update_ha_state()

    @property
    def media_position_updated_at(self):
        """
        When was the position of the current playing media valid.
        Returns value from homeassistant.util.dt.utcnow().
        """
        return self._last_position_update

    @property
    def last_changed(self):
        ''' when was the object last updated on the server'''
        return self.player_data["last_changed"]

    @property
    def unique_id(self):
        """Return the id of this roon client."""
        return self.player_data['dev_id']

    @property
    def should_poll(self):
        """Return True if entity has to be polled for state."""
        return False

    @property
    def zone_id(self):
        """ Return current session Id. """
        try:
            return self.player_data['zone_id']
        except KeyError:
            return None

    @property
    def output_id(self):
        """ Return current session Id. """
        try:
            return self.player_data['output_id']
        except KeyError:
            return None

    @property
    def name(self):
        """ Return device name."""
        try:
            return self.player_data['display_name']
        except KeyError:
            return DEVICE_DEFAULT_NAME

    @property
    def media_title(self):
        """ Return title currently playing."""
        try:
            return self.player_data['now_playing']['three_line']['line1']
        except KeyError:
            return None

    @property
    def media_album_name(self):
        """Album name of current playing media (Music track only)."""
        try:
            return self.player_data['now_playing']['three_line']['line3']
        except KeyError:
            return None

    @property
    def media_artist(self):
        """Artist of current playing media (Music track only)."""
        try:
            return self.player_data['now_playing']['three_line']['line2']
        except KeyError:
            return None

    @property
    def media_album_artist(self):
        """Album artist of current playing media (Music track only)."""
        return self.media_artist

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        try:
            image_id = self.player_data['now_playing']['image_key']
            url = self._server.roonapi.get_image(image_id)
            return url
        except KeyError:
            return None

    @property
    def media_position(self):
        """ Return position currently playing."""
        try:
            return int(self.player_data['now_playing']['seek_position'])
        except (KeyError, TypeError):
            return 0

    @property
    def media_duration(self):
        """ Return total runtime length."""
        try:
            return int(
                self.player_data['now_playing']['length'])
        except (KeyError, TypeError):
            return 0

    @property
    def media_percent_played(self):
        """ Return media percent played. """
        try:
            return (self.media_position / self.media_runtime) * 100
        except (KeyError, TypeError):
            return 0

    @property
    def volume_level(self):
        """ Return current volume level"""
        try:
            if self.player_data["volume"]["type"] == "db":
                return (int(float(self.player_data['volume']['value'] / 80) * 100) + 100) / 100
            return int(self.player_data['volume']['value']) / 100
        except (KeyError, TypeError):
            return 0

    @property
    def is_volume_muted(self):
        """ Return mute state """
        try:
            return self.player_data['volume']['is_muted']
        except (KeyError, TypeError):
            return False

    @property
    def volume_step(self):
        """ Return volume step size"""
        try:
            return int(
                self.player_data['volume']['step'])
        except (KeyError, TypeError):
            return 0

    @property
    def supports_standby(self):
        '''return power state of source controls'''
        return self._supports_standby

    @property
    def state(self):
        """ Return current playstate of the device. """
        return self._state

    @property
    def is_nowplaying(self):
        """ Return true if an item is currently active. """
        return self.state == STATE_PLAYING

    @property
    def source(self):
        """Name of the current input source."""
        return self.player_data['zone_name']
        
    @property
    def shuffle(self):
        """Boolean if shuffle is enabled."""
        try:
            return self.player_data['settings']['shuffle']
        except (KeyError, TypeError):
            return False

    @property
    def repeat(self):
        """Boolean if repeat is enabled."""
        try:
            return self.player_data['settings']['loop']
        except (KeyError, TypeError):
            return False

    def media_play(self):
        """ Send play command to device. """
        return self._server.roonapi.playback_control(self.output_id, "play")

    def media_pause(self):
        """ Send pause command to device. """
        return self._server.roonapi.playback_control(self.output_id, "pause")

    def media_play_pause(self):
        """ toggle play command to device. """
        return self._server.roonapi.playback_control(self.output_id, "playpause")

    def media_stop(self):
        """ Send stop command to device. """
        return self._server.roonapi.playback_control(self.output_id, "stop")

    def media_next_track(self):
        """ Send next track command to device. """
        return self._server.roonapi.playback_control(self.output_id, "next")

    def media_previous_track(self):
        """ Send previous track command to device. """
        return self._server.roonapi.playback_control(self.output_id, "previous")

    def media_seek(self, position):
        """ Send seek command to device. """
        return self._server.roonapi.seek(self.output_id, position)

    def set_volume_level(self, volume):
        """ Send new volume_level to device. """
        volume = int(volume * 100)
        return self._server.roonapi.change_volume(self.output_id, volume)

    def mute_volume(self, mute=True):
        """ Send mute/unmute to device. """
        return self._server.roonapi.mute(self.output_id, mute)

    def volume_up(self):
        """ Send new volume_level to device. """
        return self._server.roonapi.change_volume(self.output_id, 3, "relative")

    def volume_down(self):
        """ Send new volume_level to device. """
        return self._server.roonapi.change_volume(self.output_id, -3, "relative")

    def turn_on(self):
        """ Turn on device (if supported) """
        if self.supports_standby and 'source_controls' in self.player_data:
            for source in self.player_data["source_controls"]:
                if source["supports_standby"] and source["status"] != "indeterminate":
                    return self._server.roonapi.convenience_switch(self.output_id, source["control_key"])
        else:
            return self.async_media_play()

    def turn_off(self):
        """ Turn off device (if supported) """
        if self.supports_standby and 'source_controls' in self.player_data:
            for source in self.player_data["source_controls"]:
                if source["supports_standby"] and not source["status"] == "indeterminate":
                    return self._server.roonapi.standby(self.output_id, source["control_key"])
        else:
            return self.async_media_stop()

    def shuffle_set(self, shuffle):
        """ Set shuffle state on zone """
        return self._server.roonapi.shuffle(self.output_id, shuffle)

    def play_media(self, media_type, media_id, **kwargs):
        """
            Send the play_media command to the media player.
            Roon itself doesn't support playback of media by filename/url so this a bit of a workaround.
        """
        media_type = media_type.lower()
        if (not self._server.custom_play_action and media_type == "radio") or media_type == "radio-force":
            return self._server.roonapi.play_radio(self.zone_id, media_id)
        elif (not self._server.custom_play_action and media_type == "playlist") or media_type == "playlist-force":
            return self._server.roonapi.play_playlist(self.zone_id, media_id)
        elif self._server.custom_play_action:
            # reroute the play request to the given custom script
            _LOGGER.debug("Playback requested. Will forward to custom script/action: %s" % self._server.custom_play_action)
            data = {
                "entity_id": self.entity_id,
                "media_type": media_type,
                "media_url": media_id,
            }
            _domain, _entity = self._server.custom_play_action.split(".")
            self.hass.services.call(_domain, _entity, data, blocking=False)
            return True
        else:
            _LOGGER.info("Playback requested of unsupported type: %s --> %s" %(media_type, media_id))
            return False


class RoonServer(object):
    """Roon test."""

    def __init__(self, hass, roonapi, add_devices_callback, custom_play_action):
        """Initialize base class."""
        self.hass = hass
        self.roonapi = roonapi
        self._devices = {}
        self._last_change = None
        self._add_devices_callback = add_devices_callback
        self._update_callbacks = []
        self._init_playlists_done = False
        self._initial_playlist = None
        self._initial_player = None
        self.all_player_names = []
        self.all_playlists = []
        self.all_player_entities = []
        self.offline_devices = []
        self._selected_player = ""
        self.custom_play_action = custom_play_action
        self.roonapi.register_state_callback(self.roonapi_state_callback, event_filter=["zones_changed"])


    @property
    def devices(self):
        return self._devices

    @property
    def zones(self):
        return self.roonapi.zones

    def start_roon(self):
        '''Initialize Roon background polling'''
        ensure_future(self.do_loop())
        
    def stop_roon(self):
        '''Stop background worker'''
        self._exit = True

    def roonapi_state_callback(self, event, changed_zones):
        '''callbacks from the roon api websockets'''
        asyncio.run_coroutine_threadsafe(self.update_changed_players(changed_zones), self.hass.loop)

    def add_update_callback(self, callback, device):
        """Register as callback for when a matching device changes."""
        self._update_callbacks.append([callback, device])
        _LOGGER.debug('Added update callback for %s', device)

    def remove_update_callback(self, callback, device):
        """ Remove a registered update callback. """
        if [callback, device] in self._update_callbacks:
            self._update_callbacks.remove([callback, device])
            _LOGGER.debug('Removed update callback for %s', device)

    def _do_update_callback(self, dev_id):
        """Call registered callback functions."""
        for callback, device in self._update_callbacks:
            if device == dev_id:
                _LOGGER.debug('Call update callback for device %s', device)
                self.hass.loop.call_soon(callback, dev_id)

    @asyncio.coroutine
    def update_volume(self, dev_id, dev_name):
        ''' update volume slider if needed'''
        if self._selected_player != dev_name or not self._init_playlists_done:
            return False
        slider_vol = float(self.hass.states.get("input_number.roon_volume").state)
        output_vol = float(self._devices[dev_id].volume_level)
        if slider_vol != output_vol:
            _LOGGER.debug("player volume updated, update slider - slider_vol: %s - output_vol: %s" %(slider_vol, output_vol))
            yield from self.hass.services.async_call("input_number", "set_value", 
                    {"entity_id": "input_number.roon_volume", "value": output_vol})
        return True

    @asyncio.coroutine
    def hass_event(self, changed_entity, from_state="", to_state=""):
        _LOGGER.debug("hass_event event fired !! --> %s changed" % (changed_entity))

        if changed_entity == "input_select.roon_players":
            selected_player = to_state.state
            self._selected_player = selected_player
        else:
            selected_player = self.hass.states.get("input_select.roon_players").state
        
        if changed_entity == "input_select.roon_playlists":
            selected_playlist = to_state.state
        else:
            selected_playlist = None
        
        player_volume = 0
        player_entity = ""
        player_state = STATE_OFF

        # get player entity_id and volume level
        for dev in self._devices.values():
            if dev.name == selected_player:
                player_volume = dev.volume_level
                player_entity = dev.entity_id
                player_state = dev.state
                break
        
        # show volume slider as 0 if player is turned off
        if player_state == STATE_OFF:
            player_volume = 0

        if changed_entity == "input_select.roon_playlists" and selected_playlist == self._initial_playlist:
            # the playlist-selector was restored to default selection, ignore...
            return
        elif selected_player == self._initial_player:
            # the player-selector was restored to default selection, ignore...
            yield from self.hass.services.async_call("input_number", "set_value", 
                    {"entity_id": "input_number.roon_volume", "value": 0})
        elif changed_entity == "input_select.roon_playlists" and ": " in selected_playlist:
            # new playlist chosen, start playback
            media_content_type = selected_playlist.split(": ")[0]
            media_content_id = selected_playlist.replace(media_content_type + ": ", "")
            _LOGGER.info("start %s %s on player %s" %(media_content_type, media_content_id, player_entity))
            yield from self.hass.services.async_call("media_player", "play_media", 
                    {"entity_id": player_entity, "media_content_id": media_content_id, "media_content_type": media_content_type})
            # restore playlist selector to default value
            yield from self.hass.services.async_call("input_select", "select_option", 
                {"entity_id": "input_select.roon_playlists", "option": self._initial_playlist})
        elif changed_entity == "input_select.roon_players":
            # new player chosen - set volume slider
            _LOGGER.debug("update volumeslider for player %s to %s" %(player_entity, player_volume))
            yield from self.hass.services.async_call("input_number", "set_value", 
                {"entity_id": "input_number.roon_volume", "value": player_volume})
        
        # volume slider was adjusted
        elif changed_entity == "input_number.roon_volume" and player_entity:
            selected_volume = float(to_state.state)
            # new player chosen - set volume slider
            if selected_volume == 0:
                # volume slider set to 0, treat this as power off
                yield from asyncio.sleep(0.5, self.hass.loop)
                # double check to prevent some race condition
                if self.hass.states.get("input_select.roon_players").state == self._initial_player:
                    return
                _LOGGER.debug("turn off player %s" %(player_entity))
                yield from self.hass.services.async_call("media_player", "turn_off", 
                    {"entity_id": player_entity})
            else:
                # volume slider changed, set new volume and turn on player if needed
                if player_state == STATE_OFF:
                    _LOGGER.debug("turn on player %s" %(player_entity))
                    yield from self.hass.services.async_call("media_player", "turn_on", 
                        {"entity_id": player_entity})
                if selected_volume != player_volume:
                    _LOGGER.debug("change volume for player %s to %s" %(player_entity, selected_volume))
                    yield from self.hass.services.async_call("media_player", "volume_set", 
                        {"entity_id": player_entity, "volume_level": selected_volume})

    @asyncio.coroutine
    def do_loop(self):
        ''' background work loop'''
        _LOGGER.debug("Starting background refresh loop")
        self._exit = False
        while not self._exit:
            yield from self.update_players()
            yield from self.update_playlists()
            yield from asyncio.sleep(UPDATE_PLAYLISTS_INTERVAL, self.hass.loop)

    @asyncio.coroutine
    def update_changed_players(self, changed_zones_ids):
        """Update the players which were reported as changed by the Roon API"""
        new_devices = []
        force_playlist_update = False

        #build devices listing
        for zone_id in changed_zones_ids:
            if zone_id not in self.roonapi.zones:
                # device was removed ?
                continue
            zone = self.roonapi.zones[zone_id]
            for device in zone["outputs"]:

                dev_name = device['display_name']
                if dev_name == "Unnamed" or not dev_name:
                    # ignore unnamed devices
                    continue

                player_data = yield from self.create_player_data(zone, device)
                dev_id = player_data["dev_id"]
                player_data["is_available"] = True
                if not dev_id in self._devices:
                    # new player added !
                    _LOGGER.debug("New player added: %s" %player_data["display_name"])
                    player = RoonDevice(self, player_data)
                    new_devices.append(player)
                    self._devices[dev_id] = player
                else:
                    # device was updated
                    if dev_id in self.offline_devices:
                        _LOGGER.debug("player back online: %s" % self._devices[dev_id].entity_id)
                        force_playlist_update = True
                        self.offline_devices.remove(dev_id)
                        self._devices[dev_id].set_available(True)
                    self._devices[dev_id].update_data(player_data)
                    self._do_update_callback(dev_id)
                    yield from self.update_volume(dev_id, dev_name)

        if new_devices:
            force_playlist_update = True
            self._add_devices_callback(new_devices, True)

        if force_playlist_update and self._init_playlists_done:
            yield from self.update_playlists()


    @asyncio.coroutine
    def update_players(self):
        ''' periodic scan of all devices'''

        devs = self.roonapi.zones.keys()
        yield from self.update_changed_players(devs)

        # check for any removed devices
        for dev_id, dev in self._devices.items():
            if dev.output_id not in self.roonapi.outputs and dev_id not in self.offline_devices:
                entity_id = dev.entity_id
                if entity_id:
                    _LOGGER.info("player removed/offline: %s" % entity_id)
                    self.offline_devices.append(dev_id)
                    self._devices[dev_id].set_available(False)
                    self._do_update_callback(dev_id)


    @asyncio.coroutine        
    def update_playlists(self):
        ''' update the playlists and players input_selects'''
        try:
            if not self._initial_playlist:
                self._initial_playlist = self.hass.states.get("input_select.roon_playlists").state
            if not self._initial_player:
                self._initial_player = self.hass.states.get("input_select.roon_players").state
            volume_slider = self.hass.states.get("input_number.roon_volume").state
        except AttributeError:
            _LOGGER.warning("input_number and input_select objects do not (yet) exist. Skip playlist generation...")
            return False

        # get all current player names and entities
        all_player_names = [self._initial_player]
        all_player_entities = []
        for dev in self._devices.values():
            if dev.output_id not in self.offline_devices:
                entity_id = dev.entity_id
                if not entity_id:
                    entity_id = "media_player.%s" % dev.name.lower().replace(" ", "_")
                all_player_entities.append(entity_id)
                all_player_names.append(dev.name)

        # fill input select with player names
        if len(str(all_player_names)) != len(str(self.all_player_names)):
            # only (re)fill the listing if there are changes
            self.all_player_names = all_player_names
            yield from self.hass.services.async_call("input_select", 
                    "set_options", {"entity_id": "input_select.roon_players", "options": all_player_names})
            yield from self.hass.services.async_call("input_select", 
                    "select_option", {"entity_id": "input_select.roon_players", "option": self._initial_player})
        
        # fill group.roon_players
        if len(str(all_player_entities)) != len(str(self.all_player_entities)):
            # only (re)fill the listing if there are changes
            self.all_player_entities = all_player_entities
            yield self.hass.states.async_set("group.roon_players", "", {"entity_id": all_player_entities})
        
        # fill playlists input_select
        all_playlists = [self._initial_playlist]
        roon_playlists = self.roonapi.playlists()
        if roon_playlists:
            for item in roon_playlists["items"]:
                all_playlists.append("Playlist: %s" % item["title"])
        roon_playlists = self.roonapi.internet_radio()
        if roon_playlists:
            for item in roon_playlists["items"]:
                all_playlists.append("Radio: %s" % item["title"])
        if len(str(all_playlists)) != len(str(self.all_playlists)):
            # only send update to hass if there were changes
            self.all_playlists = all_playlists
            yield from self.hass.services.async_call("input_select", "set_options", 
                    {"entity_id": "input_select.roon_playlists", "options": all_playlists})
            yield from self.hass.services.async_call("input_select", "select_option", 
                    {"entity_id": "input_select.roon_playlists", "option": self._initial_playlist})
        
        # register callback to track state changes of our special input selects
        if not self._init_playlists_done:
            self._init_playlists_done = True
            track_entities = ["input_select.roon_playlists", "input_select.roon_players", "input_number.roon_volume"]
            event.async_track_state_change(self.hass, track_entities, self.hass_event)
        _LOGGER.debug("updated playlists")
        return True

    @asyncio.coroutine
    def create_player_data(self, zone, output):
        ''' create player object dict by combining zone with output'''
        new_dict = zone.copy()
        new_dict.update(output)
        new_dict.pop("outputs")
        new_dict["is_synced"] = len(zone["outputs"]) > 1
        new_dict["zone_name"] = zone["display_name"]
        new_dict["last_changed"] = utcnow()
        # we don't use the zone_id or output_id for now as unique id as I've seen cases were it changes for some reason
        new_dict["dev_id"] = "roon_%s" % zone["display_name"].lower().replace(" ","_").replace("-","_")
        return new_dict

