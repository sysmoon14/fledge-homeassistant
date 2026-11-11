<p align="center">
  <img src="assets/fledge.svg" width="112" height="112" alt="Fledge: a bird leaving the Nest's lit dial">
</p>

<h1 align="center">Fledge for Home Assistant</h1>

<p align="center">
  <b>Home Assistant on your Nest thermostat's own screen.</b><br>
  Turn the ring to your lights, heating zones and speakers. Press to take control.
</p>

<p align="center">
  <a href="https://github.com/sysmoon14/fledge-homeassistant/releases"><img src="https://img.shields.io/github/v/release/sysmoon14/fledge-homeassistant?color=F15A00" alt="Latest release"></a>
  <a href="https://github.com/sysmoon14/fledge-homeassistant/actions"><img src="https://img.shields.io/github/actions/workflow/status/sysmoon14/fledge-homeassistant/checks.yml?label=checks" alt="Checks"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.3%2B-41BDF5" alt="Home Assistant 2026.3 or later">
</p>

<p align="center">
  <img src="assets/screens-fledge.png" width="100%" alt="Five screens in Fledge's look: the thermostat heating with 22 minutes to go, a lamp at 60%, a speaker's album art, the glance with the time and weather, and the thermostat's Settings.">
</p>

Walk up to the thermostat and it wakes to your devices, zoomed out around the
thermostat itself, each card's name above it. Turn to browse, press to open;
nothing is switched or run from the cards themselves, only once a card is open.
Open, a press shows its options and a long press does its main action:

| Card | Press | Turn | Long press | Its options |
|-|-|-|-|-|
| **Light** | its dimmer | brightness | on / off | on / off, **warmth** and **colour** dials where it has them, and a group's **each light** |
| **Switch, fan** | its switch | | on / off | |
| **Other thermostat** | its target | the temperature, as the Nest sets its own | | its presets: eco, away… |
| **Speaker** | its volume | the volume | play / pause | play / pause, previous, next; album art and progress on the card |
| **Option (select)** | its options | through them | | e.g. the Nest's hot water boost |
| **Blind, cover** | how open | the position | open / close | open, close |
| **Scene, script, button** | opens it | | runs it | Run |
| **Camera** | a fresh picture | | | its latest still fills the card |
| **Alarm** | its state | the option, or each digit of the code | | arm (its modes, named as you like), Disarm or Cancel; the code entered on the ring |
| **The Nest itself** | the Nest as it always was | | | Back in its menu returns to the cards |

An alarm works with any alarm panel in Home Assistant. Its code is entered
with the ring: the digits sit round it, turn to each and press, hold to rub one
out. When the entry delay starts, the thermostat lights straight into the code
pad, the ring counting down; going off, it shows red until disarmed (or, with
**Take over the screen** off in Fledge's panel, it waits on its card). With
[Alarmo](https://github.com/nielsfaber/alarmo) the countdown is in seconds, the
screen names the sensor that set it off, and a door left open can be armed
past. Three wrong codes lock the pad for 30 seconds, longer each time; the code
is never stored. In Fledge's panel, name each mode for the Nest (or copy the
names from an Alarmo card) and say how long the code is, so the last digit
sends it. Alarm cards need the thermostat on Fledge v0.7.0 or later.

What you open stays open until you go back with its options' Back (a long
press on the cards themselves puts the screen away); left alone, the screen
times out, and the next wake starts again from the top. Everything is drawn
on the thermostat at 60
frames a second, with its clicks, in Fledge's look (above) or, one setting away
(Settings → Theme on the thermostat), the Nest's own (below). The Nest keeps
running the heating throughout.

<p align="center">
  <img src="assets/screens.png" width="100%" alt="Five screens in the Nest's own look: the device chooser on a reading lamp's card, the lamp's warmth dial, another heating zone that is heating, a speaker's album art and progress, and the lamp's colour dial.">
</p>

This is the Home Assistant half of **[Fledge](https://github.com/sysmoon14/fledge)**,
which roots the thermostat and runs it locally. It needs a Nest Learning
Thermostat (3rd generation) running Fledge, connected to Home Assistant over MQTT.

## Install

1. **Fledge on the thermostat**, with MQTT: it shows up in Home Assistant as a
   "Nest Thermostat" device. See [Fledge](https://github.com/sysmoon14/fledge#get-started).
2. **This integration:** open it in the Marketplace (or HACS) with this button,
   download **Fledge**, and restart Home Assistant.

   [![Open the Fledge repository in Home Assistant's Marketplace (HACS)](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sysmoon14&repository=fledge-homeassistant&category=integration)

   Or add `https://github.com/sysmoon14/fledge-homeassistant` there by hand, as a
   custom repository of type Integration.
3. **Set it up:** Home Assistant offers the thermostat under Settings →
   Devices & services, or add it with this button. Pick your devices, in the
   order you want them, and what the thermostat wakes to.

   [![Add the Fledge integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=fledge)

Change them any time from **Fledge** in the sidebar (admins only): drag the
cards into the ring's order (the thermostat's own card too, from Fledge v0.5 on
the Nest; Settings stays last), rename one for the Nest or the thermostat
itself, choose one to wake straight to, and turn a ring on the page to see each
card drawn as the Nest draws it, in its Fledge look (the default) or the Nest's. The same tabs set
the wake and glance, try a notification, and show the thermostat's builds, its
update and a check for one. The integration's **Configure** still works too.
A light group makes a whole room one card. Removing the integration puts the
thermostat back as it was.

## Make it yours with automations

The integration adds a **Nest Thermostat cards** device, connected to the
thermostat's own, with:

| Entity | What it does |
|-|-|
| `select` **Wake to** | What the thermostat shows as it wakes: the device chooser, **Automatic** (the speaker that's playing, else after sunset a light that's on, else the chooser), the thermostat as the Nest always did, or any of your devices. |
| `switch` **Keep screen on** | Keeps the thermostat's display lit. |
| `button` **Wake screen** | Lights the display onto what it wakes to. |
| `select` **Glance layout** | The glance's layout: Clock, Temperature, Weather, Analog, Big number or Up next. Each keeps the lines you chose for it. |
| `binary_sensor` **Night glance** | On while the Nest shows the glance's night layout. |

In **Configure**, **A glance as someone comes near** makes the Nest, when it
lights up because you walked up (not a touch), show the time, the weather (from
a weather entity you pick) and what's on; a touch shows what it wakes to.

The panel's **Glance** tab chooses what the glance shows, with a preview of the
Nest's screen and **Try on the Nest** to see it for real for 20 seconds before
saving:

- **Layout**: the time large (Clock), the room's temperature (Temperature,
  orange or blue while heating or cooling), the weather outside, a clock face
  (Analog), one sensor's value (Big number) or your next calendar event (Up next).
- **Clock**: 12 or 24 hours, or Automatic from where Home Assistant is, and AM / PM.
- **Lines**: up to three under the big element. The Nest works out the date, the
  time, its temperature and humidity, its heating and time to temperature, the
  weather and what's on itself. Home Assistant sends today's high and low, when
  rain arrives, sunrise or sunset, what a speaker you choose is playing, your
  next event, who's home, any entity's value (a door says Open, a timestamp
  reads as a time), or a template. A sensor that changes every second reaches
  the Nest at most every 15 seconds.
- **At night**: a different layout and lines, from sunset to sunrise (shifted
  by up to three hours), between set times, or while a sensor such as a bedtime
  switch is on (or off), drawn dimmer and warmer if you like.

The glance's layouts need the Nest on Fledge 0.9.0 or later; an older build
shows the clock as before.

So the thermostat can show what matters right now, for example the speaker
while music plays, and the device chooser otherwise:

```yaml
automation:
  - alias: Nest shows the music while it plays
    triggers:
      - trigger: state
        entity_id: media_player.kitchen_speaker
    actions:
      - action: select.select_option
        target:
          entity_id: select.nest_thermostat_cards_wake_to
        data:
          option: "{{ 'Kitchen speaker' if is_state('media_player.kitchen_speaker', 'playing') else 'Device chooser' }}"
```

Or keep the screen on while you cook, light it when the doorbell rings, or wake
it to the lights at sunset.

## Notifications on the Nest

`fledge.notify` puts a message on the thermostat's screen, waking it with a
chime: an icon, a title and a message, or a camera's picture, taken there and
then, filling the round screen. Press to answer it, hold to dismiss it; either
fires a `fledge_notification` event with its tag, so an automation can act on
the answer. It dismisses itself after `timeout` seconds, and `fledge.dismiss`
takes it back. With more than one Nest, `thermostat` picks which ones show it
(all of them when it's left out).

**The doorbell:** someone rings, and the thermostat shows who's there.

```yaml
automation:
  - alias: Doorbell on the Nest
    triggers:
      - trigger: state
        entity_id: binary_sensor.front_door_ding
        to: "on"
    actions:
      - action: fledge.notify
        data:
          title: Someone's at the front door
          message: Front door
          camera: camera.front_door
          tag: doorbell
          timeout: 60
```

**An answer:** a press on the Nest locks the back door.

```yaml
automation:
  - alias: Back door left open
    triggers:
      - trigger: state
        entity_id: binary_sensor.back_door
        to: "on"
        for: "00:10:00"
    actions:
      - action: fledge.notify
        data:
          title: Back door open
          message: Press to lock it when it's shut.
          icon: mdi:door-open
          tag: back_door
      - wait_for_trigger:
          - trigger: event
            event_type: fledge_notification
            event_data: { tag: back_door, action: pressed }
        timeout: "00:02:00"
        continue_on_timeout: false
      - action: lock.lock
        target:
          entity_id: lock.back_door
```

## Weather and temperature

The weather entity you pick (in the panel's **Thermostat** tab, or the
integration's options) feeds the Nest's own weather screen too: the outside
temperature and humidity, and the forecast, every 15 minutes. Pick an outside
thermometer as well to use its temperature instead of the forecast's. This
replaces the Nest weather blueprint.

**Room thermometer:** the Nest regulates from a Home Assistant sensor instead
of its own, in °C or °F, and goes back to its own if the readings stop for 30
minutes. The Nest needs Fledge's external temperature option for this, which
turns off its Bluetooth, so real Nest Temperature Sensors stop working. Turn it
on with the Nest's **External temperature** switch, the toggle in the panel, or
the repair Home Assistant shows when a room thermometer is chosen without it.
Changing it reboots the Nest, so its heating pauses for about a minute.

**Heating today** and **Hot water today** count the hours the Nest has called
for each since midnight. They're kept as long-term statistics, so a statistics
graph card shows them by day, week or month.

## The Nest's schedules on your dashboards

Fledge brings two dashboard cards for the Nest's own weekly schedules, heating
and hot water. There's nothing to add under Resources: they load with the
integration.

```yaml
type: custom:fledge-heating-schedule
entity: climate.nest_thermostat
```

```yaml
type: custom:fledge-hot-water-schedule
entity: climate.nest_thermostat
```

Each shows the week, today and the time now marked. Tap a day or Edit schedule
to change it: a day at a time, with every changed day saved together. **Copy
to…** gives the day you're on to any others (Weekdays, Weekend, Every day, or
picked one by one) in a single write, with Undo for a few seconds after. On the
hot water card the pill pauses and resumes the schedule; on the heating card it
opens the thermostat.

The editor opens in the card's own dialog. To use your own popup instead, give
the card `edit: '#your_popup'` and put `custom:fledge-heating-schedule-editor`
(or `fledge-hot-water-schedule-editor`) with the same `entity` in that popup.

The cards write with `fledge.set_schedule`, which automations can use too. It
replaces the days it names and keeps the rest:

```yaml
action: fledge.set_schedule
data:
  entity_id: climate.nest_thermostat
  heating:
    sat: [{ time: "08:00", temp: 21 }, { time: "22:30", temp: 16 }]
    sun: [{ time: "08:00", temp: 21 }, { time: "22:30", temp: 16 }]
  hot_water:
    sat: [{ on: "07:00", off: "08:00" }]
```

Writing the heating schedule turns the Nest's Auto-Schedule off, as any edit of
its schedule does, and sets the temperature that applies now unless the Nest is
in Eco.

## Updates

This integration and the thermostat's software are released apart, and talk
only over MQTT:

- **This integration** updates from the Marketplace. When it does, it has the
  thermostat look for its own update straight away, as the two often come out
  together.
- **The thermostat** updates from its own update entity (Settings → Updates),
  signed, and rolls itself back if the new build doesn't come up.
- **If they don't match**, the thermostat says which version of the cards it
  understands, and a repair under Settings → Repairs asks you to update it.

## What the thermostat may do

Only to the devices chosen here, and only: switch a light, switch or fan on or
off; set a light's brightness, warmth or colour; set a thermostat's temperature;
set a media player's volume, and play or pause it; run a scene, script or
button; take a camera's picture. Anything else it asks for is refused and
logged. A notification's answer only fires an event, for your automations.

## Security

The thermostat and this integration talk over your MQTT broker, so whoever
can publish to it can do what the thermostat can: anything listed above, to
the devices you chose (a garage door among them, if you add one), and put
messages on its screen. Give the broker passwords (and TLS if you can), and if
it supports ACLs, let only Home Assistant publish to `nestlocal/+/set/#` and
only the thermostat's user publish to `nestlocal/+/ui/#`. A notification's
answer is only taken while that notification is showing.

## Development

```sh
uv venv --python 3.13 .venv && uv pip install -r requirements_test.txt
.venv/bin/pytest
```

Checks run on every push: the tests, Home Assistant's hassfest, and HACS validation.

## License

GPL-3.0, as [Fledge](https://github.com/sysmoon14/fledge). Fledge is not
affiliated with, endorsed or sponsored by Google LLC or Nest Labs. Nest and Nest
Learning Thermostat are trademarks of Google LLC.
