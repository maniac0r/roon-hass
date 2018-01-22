"""
Support to interface with the Roon API.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.roon/
"""
import voluptuous as vol
import logging
import asyncio
import json
import aiohttp
import async_timeout
import time

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

DEFAULT_HOST = 'localhost'
DEFAULT_PORT = 3006
TIMEOUT = 10
POLL_INTERVAL = 2
UPDATE_PLAYLISTS_INTERVAL = 300
CONF_NOTIFY = "enable_notifications"

SUPPORT_ROON = SUPPORT_PAUSE | SUPPORT_VOLUME_SET | SUPPORT_SELECT_SOURCE | SUPPORT_STOP | \
    SUPPORT_PREVIOUS_TRACK | SUPPORT_NEXT_TRACK | SUPPORT_SHUFFLE_SET | \
    SUPPORT_SEEK | SUPPORT_TURN_ON | SUPPORT_TURN_OFF | SUPPORT_VOLUME_MUTE | \
    SUPPORT_PLAY | SUPPORT_PLAY_MEDIA

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_HOST, default=DEFAULT_HOST): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_NOTIFY, default=False): cv.boolean,
})


@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the Roon platform."""
    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)
    notifications = config.get(CONF_NOTIFY)
    _LOGGER.info("Creating RoonServer object for %s", host)
    roon = RoonServer(hass, host, port, notifications, async_add_devices)

    @asyncio.coroutine
    def stop_roon(event):
        """Stop Roon connection."""
        _LOGGER.info("stop requested")
        roon.stop_roon()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_roon)
    roon.start_roon()


class RoonDevice(MediaPlayerDevice):
    """Representation of an Roon device."""

    def __init__(self, server, player_data):
        """Initialize Roon device object."""
        self._sources = []
        self._server = server
        self._available = True
        self._device_id = player_data["output_id"]
        self._last_position_update = None
        self._supports_standby = False
        self._state = STATE_IDLE
        self.update_data(player_data)
        _LOGGER.info("New Roon Device initialized with ID: %s", self._device_id)

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
        _LOGGER.debug("async_update called for %s" %self.entity_id)
        self.update_data(self.player_data)


    def update_data(self, player_data=None):
        """ Update session object. """
        _LOGGER.debug("update_data called for %s" %self.entity_id)
        if player_data:
            self.player_data = player_data
        # create sources list
        self._sources = self.get_sync_zones()
        self._available = self.player_data["is_available"]
        if self.state == STATE_PLAYING:
            self._last_position_update = utcnow()
        # determine player state
        self.update_state()
        
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
                            if source["status"] == "standby":
                                new_state = STATE_OFF
                            break
            # determine player state
            if not new_state:
                if self.player_data['state'] == 'playing':
                    new_state = STATE_PLAYING
                elif self.player_data['state'] == 'stopped':
                    new_state = STATE_IDLE
                elif self.player_data['state'] == 'paused':
                    new_state = STATE_PAUSED
                else:
                    new_state = STATE_IDLE
            self._state = new_state


    @asyncio.coroutine
    def async_added_to_hass(self):
        """Register callback."""
        self._server.add_update_callback(
            self.async_update_callback, self.output_id)

    @callback
    def async_update_callback(self, msg):
        """Handle device updates."""
        self.async_schedule_update_ha_state()

    def get_sync_zones(self):
        ''' get available sync slaves'''
        sync_zones = [self.name]
        for output in self.player_data["can_group_with_output_ids"]:
            for zone in self._server.zones.values():
                if output == zone["output"] and zone['name'] not in sync_zones:
                    sync_zones.append( zone["name"] )
        _LOGGER.debug("sync_slaves for player %s: %s" % (self.name, sync_zones))
        return sync_zones

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
        return '{}.{}'.format(self.__class__, self._device_id)

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
            url = 'http://{}:{}/image?image_key={}&width=500&height=500&scale=fit'.format(
                self._server.host, self._server.port, image_id)
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
                return int(self.player_data['volume']['value'] + 80) / 100
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
        """ Return total runtime length."""
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
    def source_list(self):
        """List of available input sources."""
        return self._sources
        
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

    @property
    def auto_radio(self):
        """Boolean if auto_radio is enabled."""
        try:
            return self.player_data['settings']['auto_radio']
        except (KeyError, TypeError):
            return False

    def async_media_play(self):
        """ Send play command to device. """
        return self._server.async_query("control", {"control":"play", "zone": self.output_id} )

    def async_media_pause(self):
        """ Send pause command to device. """
        return self._server.async_query("control", {"control":"pause", "zone": self.output_id} )

    def async_media_play_pause(self):
        """ toggle play command to device. """
        return self._server.async_query("control", {"control":"playpause", "zone": self.output_id} )

    def async_media_stop(self):
        """ Send stop command to device. """
        return self._server.async_query("control", {"control":"stop", "zone": self.output_id} )

    def async_media_next_track(self):
        """ Send next track command to device. """
        return self._server.async_query("control", {"control":"next", "zone": self.output_id} )

    def async_media_previous_track(self):
        """ Send previous track command to device. """
        return self._server.async_query("control", {"control":"previous", "zone": self.output_id} )

    def async_media_seek(self, position):
        """ Send seek command to device. """
        return self._server.async_query("seek", {"seek":position, "zone": self.output_id} )

    def async_set_volume_level(self, volume):
        """ Send new volume_level to device. """
        volume = int(volume * 100)
        return self._server.async_query("change_volume", {"volume":volume, "output": self.output_id} )

    def async_mute_volume(self, mute=True):
        """ Send new volume_level to device. """
        value = "mute" if mute else "unmute"
        return self._server.async_query("mute", {"how":value, "output": self.output_id} )

    def async_volume_up(self):
        """ Send new volume_level to device. """
        new_vol = self.volume_level + 0.05
        if new_vol < 1:
            return self.async_set_volume_level(new_vol)
        else:
            return False

    def async_volume_down(self):
        """ Send new volume_level to device. """
        new_vol = self.volume_level - 0.05
        if new_vol > 0:
            return self.async_set_volume_level(new_vol)
        else:
            return False

    def async_turn_on(self):
        """ Turn on device (if supported) """
        if not self.supports_standby:
            return self._server.async_query("control", {"control":"play", "zone": self.output_id} )
        else:
            return self._server.async_query("convenience_switch", {"output": self.output_id} )

    def async_turn_off(self):
        """ Turn off device (if supported) """
        if not self.supports_standby:
            return self._server.async_query("control", {"control":"stop", "zone": self.output_id} )
        else:
            return self._server.async_query("standby", {"output": self.output_id} )

    def async_shuffle_set(self, shuffle):
        """ Set shuffle state on zone """
        return self.change_settings("shuffle", shuffle)

    def async_set_shuffle(self, shuffle):
        """ Set shuffle state on zone """
        return self.change_settings("shuffle", shuffle)

    def set_auto_radio(self, enabled=False):
        """ Enable the auto radio function on zone """
        return self.change_settings("auto_radio", enabled)

    def toggle_repeat(self):
        """ 
            Toggle repeat on zone 
            Possible values: 'loop' | 'loop_one' | 'disabled' | 'next'
        """
        return self.change_settings("loop", "next")

    def change_settings(self, setting, value):
        """ Send new volume_level to device. """
        return self._server.async_query("change_settings", {"zone": self.zone_id, "setting": setting, "value": value} )


    def async_select_source(self, source):
        '''select source on player (used to sync/unsync)'''
        if source == self.name:
            return self._server.async_query("ungroup_output", {"output": self.output_id } )
        else:
            for zone_id, zone in self._server.zones.items():
                if zone["name"].lower() == source.lower():
                    _LOGGER.info("select source called - sync %s with %s" %(self.name, zone["name"]))
                    return self._server.async_query("add_to_group", {"output": self.output_id, "zone": zone_id } )
        return None


    def async_play_media(self, media_type, media_id, **kwargs):
        """
        Send the play_media command to the media player.
        """
        if self.state == STATE_OFF:
            yield from self.async_turn_on()
            time.sleep(1)
        media_type = media_type.lower()
        if media_type == "radio":
            yield from self._server.async_query("play/radio", {"name": media_id, "zone": self.zone_id } )
        elif media_type == "playlist":
            yield from self._server.async_query("play/playlist", {"name": media_id, "zone": self.zone_id } )
        elif media_id.startswith("http") and self._server.notifications_enabled:
            _LOGGER.info("Playback requested of notification ! %s --> %s" %(media_type, media_id))
            yield from self.hass.services.async_call("mqtt", "publish", 
                {"topic": "%s/notify" % self.name.lower(), "payload": media_id, "retain": False})
            _LOGGER.info(self.name.lower())
            return False
        else:
            _LOGGER.info("Playback requested of unsupported type: %s --> %s" %(media_type, media_id))
            return False


class RoonServer(object):
    """Roon test."""

    def __init__(self, hass, host, port, notifications, add_devices_callback):
        """Initialize base class."""
        self.hass = hass
        self.host = host
        self.port = port
        self._zones = {}
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
        self.notifications_enabled = notifications


    @property
    def devices(self):
        return self._devices

    @property
    def zones(self):
        return self._zones

    def start_roon(self):
        '''Initialize Roon background polling'''
        ensure_future(self.do_loop())
        

    def stop_roon(self):
        '''Stop background worker'''
        self._exit = True


    def add_update_callback(self, callback, device):
        """Register as callback for when a matching device changes."""
        self._update_callbacks.append([callback, device])
        _LOGGER.debug('Added update callback to %s on %s', callback, device)

    def remove_update_callback(self, callback, device):
        """ Remove a registered update callback. """
        if [callback, device] in self._update_callbacks:
            self._update_callbacks.remove([callback, device])
            _LOGGER.debug('Removed update callback %s for %s',
                          callback, device)

    def _do_update_callback(self, dev_id):
        """Call registered callback functions."""
        for callback, device in self._update_callbacks:
            if device == dev_id:
                _LOGGER.debug('Update callback %s for device %s by %s',
                              callback, device, dev_id)
                self.hass.loop.call_soon(callback, dev_id)

    @asyncio.coroutine
    def update_volume(self, output_data):
        ''' update volume slider if needed'''
        if self._selected_player != output_data['display_name'] or not self._init_playlists_done:
            return False
        slider_vol = float(self.hass.states.get("input_number.roon_volume").state)
        try:
            if output_data["volume"]["type"] == "db":
                output_vol = float(output_data['volume']['value'] + 80) / 100
            else:
                output_vol = float(output_data['volume']['value']) / 100
        except (KeyError, TypeError):
            output_vol = 0
        if slider_vol != output_vol:
            _LOGGER.info("player volume updated, update slider")
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
            media_content_id = selected_playlist.split(": ")[1]
            media_content_type = selected_playlist.split(": ")[0]
            _LOGGER.debug("start %s %s on player %s" %(media_content_type, media_content_id, player_entity))
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
                _LOGGER.info("turn off player %s" %(player_entity))
                yield from self.hass.services.async_call("media_player", "turn_off", 
                    {"entity_id": player_entity})
            else:
                # volume slider changed, set new volume and turn on player if needed
                if player_state == STATE_OFF:
                    _LOGGER.info("turn on player %s" %(player_entity))
                    yield from self.hass.services.async_call("media_player", "turn_on", 
                        {"entity_id": player_entity})
                if selected_volume != player_volume:
                    _LOGGER.info("change volume for player %s to %s" %(player_entity, selected_volume))
                    yield from self.hass.services.async_call("media_player", "volume_set", 
                        {"entity_id": player_entity, "volume_level": selected_volume})


    @asyncio.coroutine
    def do_loop(self):
        '''refresh players loop'''
        _LOGGER.debug("Starting background refresh loop")
        playlist_update_ticks = UPDATE_PLAYLISTS_INTERVAL
        
        self._exit = False
        while not self._exit:
            # update players every poll interval
            yield from self.update_players()
            # update playlists and players group every minute
            if playlist_update_ticks >= (UPDATE_PLAYLISTS_INTERVAL / POLL_INTERVAL):
                playlist_update_ticks = 0
                yield from self.update_playlists()
            else:
                playlist_update_ticks += 1
            yield from asyncio.sleep(POLL_INTERVAL, self.hass.loop)


    @asyncio.coroutine
    def update_players(self):
        """Create a list of outputs connected to Roon."""
        new_devices = []
        cur_devices = []
        force_playlist_update = False
        result = yield from self.async_query('zones')
        if not result or result["last_change"] == self._last_change:
            return False # no update needed
        self._last_change = result["last_change"]
        zones = result["zones"]
        
        # build zones listing
        cur_zones = {}
        for zone in zones.values():
            output = zone['outputs'][0]['output_id'] # first output is the sync master
            cur_zones[zone["zone_id"]] = {"name": zone["display_name"], "output": output}
        self._zones = cur_zones

        #build devices listing
        for zone in zones.values():
            for device in zone["outputs"]:

                if device['display_name'].startswith('sync_'):
                    # ignore sync devices
                    continue
                player_data = yield from self.createplayer_data(zone, device)
                dev_id = player_data["output_id"]
                cur_devices.append(dev_id)
                player_data["is_available"] = True
                if not dev_id in self._devices:
                    # new player added !
                    _LOGGER.info("New player added: %s" %player_data["display_name"])
                    player = RoonDevice(self, player_data)
                    new_devices.append(player)
                    self._devices[dev_id] = player
                elif player_data["last_changed"] != self._devices[dev_id].last_changed:
                    # device was updated
                    if dev_id in self.offline_devices:
                        _LOGGER.info("player back online: %s" % self._devices[dev_id].entity_id)
                        force_playlist_update = True
                        self.offline_devices.remove(dev_id)
                        self._devices[dev_id].set_available(True)
                    self._devices[dev_id].update_data(player_data)
                    self._do_update_callback(dev_id)
                    yield from self.update_volume(player_data)
                    
        # check for any removed devices
        for dev_id in self._devices.keys():
            if dev_id not in cur_devices and dev_id not in self.offline_devices:
                entity_id = self._devices[dev_id].entity_id
                if entity_id:
                    _LOGGER.info("player removed/offline: %s" %entity_id)
                    self.offline_devices.append(dev_id)
                    self._devices[dev_id].set_available(False)
                    self._do_update_callback(dev_id)
                    force_playlist_update = True

        if new_devices:
            force_playlist_update = True
            self._add_devices_callback(new_devices, True)

        if force_playlist_update:
            yield from self.update_playlists()


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
        roon_playlists = yield from self.async_query("browse/playlists")
        if roon_playlists:
            all_playlists = [self._initial_playlist]
            for item in roon_playlists["playlists"]:
                all_playlists.append("Playlist: %s" %item)
            for item in roon_playlists["radios"]:
                all_playlists.append("Radio: %s" % item)
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
    def async_query(self, endpoint, params=None):
        """Abstract out the JSON connection."""
        url = "http://{}:{}/{}".format(
            self.host, self.port, endpoint)
        params = params if params else {}
        data = {}
        try:
            websession = async_get_clientsession(self.hass)
            with async_timeout.timeout(TIMEOUT, loop=self.hass.loop):
                response = yield from websession.get(url, params=params)
                if response.status != 200:
                    _LOGGER.error(
                        "Query failed, response code: %s Full message: %s",
                        response.status, response)
                data = yield from response.json()
        except Exception as error:
            _LOGGER.debug("Failed to retrieve data for endpoint %s" %endpoint, type(error))
        return data

    @asyncio.coroutine
    def createplayer_data(self, zone, output):
        ''' create player object dict by combining zone with output'''
        new_dict = zone.copy()
        new_dict.update(output)
        new_dict.pop("outputs")
        new_dict["is_synced"] = len(zone["outputs"]) > 1
        new_dict["zone_name"] = zone["display_name"]
        return new_dict

