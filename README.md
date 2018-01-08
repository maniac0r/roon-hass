## Home Assistant component for Roon

Custom component for Home Assistant (www.home-assistant.io) to control Roon (www.roonlabs.com) zones as mediaplayers.


## Installation

While this is a work in progress, the component must be installed as a custom component on your hass setup.
Once stable, it will be submitted to the hass source for inclusion.

For now, the python code of this hass component is not directly talking to the Roon api as there is not yet a python SDK/API released by Roon itself.
So for the time being a intermediate webservice is used which talks to the nodeJS api making it available as restfull services for the python code.
It's my plan to replace this by direct api calls once I get some more info about the websockets api that Roon provides.


1. Download and install the webproxy
    Download and install the api proxy nodeJS module from https://github.com/marcelveldt/roon-extension-api-proxy
    Make sure it's up and running (by default on port 3006) before you continue.


2. Download/install the hass component

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
        port: 3006
    ```

    For the host and port parameters give the host where you are running the NodeJS proxy from step 1.
    You can ommit host if it's running on the same host as hass.
    You can ommit port if it's setup at the default port 3006.


3. Done !

    Now restart Home Assistant and within a few seconds after hass is started, your players should appear.


## What is supported ?

* All player command are supported, like controlling the volume, play/pause, next etc.
* Each player represents a "Roon output". A zone with multiple outputs will be displayed as multiple media players in hass.
* The source of each hass media player represents the Roon zone it's attached to.
* You can change the source of the hass mediaplayer to group the output into another zone (zone grouping)
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

* Connect to Roon websockets api directly instead of using the nodeJS proxy.
* Support playback of media files (if only I could find out how in the api)
* Support text to speach playback
* Support of notifications and alarm signals on Roon zones.
* Optimize zones adding/removal.
* cleanup code / pep8 compliance






