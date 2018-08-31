## Home Assistant component for Roon

Custom component for Home Assistant (www.home-assistant.io) to control Roon (www.roonlabs.com) zones as mediaplayers.


## Installation

While this is a work in progress, the component must be installed as a custom component on your hass setup.
Once stable, it will be submitted to the hass source for inclusion.

Update August 2018: The add-on is almost ready for prime time (release to home assistant as official addon).
It's talking directly to the Roon server now (using the tcp socket) so no more need for the nodejs proxy.


1. Download/install the hass component

   * Download roon.py of this Github repo into a local folder. 

   TIP: Click the green 'Clone or Download' button and select 'Download ZIP' and extract the file from there.

   * In your Home Assistant configuration directory (where all the yaml files reside), create a directory 'custom_components'

   * Inside the 'custom_components' folder, create a subfolder 'media_player'

   * Put the roon.py file inside this directory.

   * Add the roon component in your hass configuration. In your configuration.yaml add an entry for Roon:

   ```
    media_player:
      - platform: roon
        host: hostname_or_ip
    ```

    For the host and port parameters give the host where you are Roon.
    You can ommit host if you only have 1 Roon server in your network, it will be auto discovered.

3. Almost Done !

    Now restart Home Assistant and approve the addon within Roon (extensions section).


3. Done !

    After the approval, within a few seconds your players should appear in Home Assistant.


## What is supported ?

* All player command are supported, like controlling the volume, play/pause, next etc.
* Each player represents a "Roon output". A zone with multiple outputs will be displayed as multiple media players in hass.
* The source of each hass media player represents the Roon zone it's attached to.
* You can start playback of Playlists or Internet Radio, playback of other content is not yet supported.
* To start a playlist, set the name of the playlist as the "media_content_id" and set "media_content_type" to "playlist".
* To start a radio, set the name of the radio station as the "media_content_id" and set "media_content_type" to "radio".
* Use the usual Hass configuration to control your media players with Alexa or Homekit.
* The code should be fuly async safe so it should not hog the hass event loop.
* New players will be auto detected, no need to restart hass.


## Bonus: player widget for hass frontend
For my own usecase I've added some code to automate some stuff with the mediaplayers to have some sort of "easy access" controls available in the hass frontend.

1. group.roon_players: This group will be auto added and contains all your Roon media players.

2. players and playlists widget: 
    Add the following code to your hass configuration.yaml:
    ```
    input_number:
      roon_volume:
        name: Player volume
        icon: mdi:volume-high
        min: 0
        max: 1
        step: 0.01
    input_select:
      roon_playlists:
        name: 'Playlist:'
        options:
          - Select playlist
        initial: Select playlist
        icon: mdi:spotify
      roon_players:
        name: 'Room:'
        options:
          - Select room
        initial: Select room
        icon: mdi:speaker-wireless
    ```

    Feel free to customize the name and icons to your liking, as long as the id is the same.
    These input selects will be auto filled by the component with your playlists and players.
    To display the widget somewhere in the frontend:

    Create a group with these components:

    ```
      roon_actions:
        name: Music players
        entities:
          - input_select.roon_players
          - input_select.roon_playlists
          - input_number.roon_volume
    ```

    Now add this group (group.roon_actions) to one of your views.
    
    Offcourse if you ommit these objects, this part of the code won't be used at all.


## Feedback and TODO

This is considered to be a work in progress. I'm sure there will be some bugs in the code somewhere.
Let's test it, fix it and when stable enough submit it to Home Assistant for inclusion (with Roon's approval offcourse).

Please use the Roon forum to discuss the progress:

https://community.roonlabs.com/t/roon-module-for-home-assistant


TO-DO:

* [DONE] Connect to Roon websockets api directly instead of using the nodeJS proxy.
* [NOT POSSIBLE] Support playback of media files (if only I could find out how in the api)
* Support text to speech playback [DONE WITH WORKAROUND - more info soon]
* Support of notifications and alarm signals on Roon zones. [DONE WITH WORKAROUND - more info soon]
* cleanup code / pep8 compliance






