"""Constants for Fledge, the companion of a Nest thermostat running Fledge."""

DOMAIN = "fledge"

# nestlocal, on the thermostat, publishes and listens under nestlocal/<serial>.
TOPIC_PREFIX = "nestlocal"

CONF_SERIAL = "serial"
# The devices the thermostat shows, a card each, in order.
CONF_ENTITIES = "entities"
# What it wakes to: the chooser or itself, unless CONF_WAKE_DEVICE names a card.
CONF_WAKE = "wake"
CONF_WAKE_DEVICE = "wake_device"
# A glance as someone comes near (the time, the weather, what's on), and
# the weather entity it shows.
CONF_GLANCE = "glance"
CONF_WEATHER = "weather"
# Someone across the room (the Nest's Farsight lights it) and someone close
# (its approach reading), and how long the cards stay untouched: protocol 6.
# Without CONF_FAR, the glance switch says (glance, else wake).
CONF_FAR = "far"
CONF_NEAR = "near"
CONF_CARD_IDLE = "card_idle"
FAR_GLANCE, FAR_WAKE, FAR_DARK = "glance", "wake", "dark"
NEAR_WAKE, NEAR_GLANCE, NEAR_NONE = "wake", "glance", "none"
CARD_IDLE = 20
# What a card is called on the Nest, where not its friendly name: by entity.
CONF_NAMES = "names"
# Where the thermostat's own card goes among them: before entities[n] (0, first).
CONF_THERMOSTAT = "thermostat"
# Before 0.4.4: rooms' lights, less some, then thermostats and media players.
CONF_AREAS = "areas"
CONF_EXCLUDE = "exclude"
CONF_CLIMATES = "climates"
CONF_MEDIA = "media"

WAKE_CHOOSER = "chooser"
WAKE_THERMOSTAT = "thermostat"
# The speaker playing, else after sunset a light that's on, else the chooser.
WAKE_AUTO = "auto"

# What can be a card: switched (and dimmed, a light), set, or turned up.
SWITCHED = ("light", "switch", "fan", "input_boolean")
# Pressed, they run: the service each takes.
RUN = {"scene": "turn_on", "script": "turn_on", "button": "press", "input_button": "press"}
# A choice of options, and covers set part open.
CHOICES = ("select", "input_select")
DOMAINS = [*SWITCHED, "climate", "media_player", *RUN, "camera", *CHOICES, "cover"]

# The cards the thermostat shows (retained) and what it asks for.
CARDS_TOPIC = "{prefix}/{serial}/set/ui"
COMMAND_TOPIC = "{prefix}/{serial}/ui/command"
# What of the cards the thermostat's build understands (retained).
PROTOCOL_TOPIC = "{prefix}/{serial}/ui/protocol"
# What this integration needs of it: nestlocal's ui::PROTOCOL.
PROTOCOL = 6
# Asks the thermostat to look for its own update now (its button's command).
CHECK_TOPIC = "{prefix}/{serial}/set/check_update"
# Keeps the thermostat's display lit (ON/OFF, retained), and wakes it.
SCREEN_ON_TOPIC = "{prefix}/{serial}/set/screen_on"
SCREEN_WAKE_TOPIC = "{prefix}/{serial}/set/screen_wake"


def presence(options) -> tuple[str, str, int]:
    """What someone across the room and someone close bring, and how long
    the cards stay untouched (seconds), from an entry's options."""
    far = options.get(CONF_FAR) or (FAR_GLANCE if options.get(CONF_GLANCE) else FAR_WAKE)
    return far, options.get(CONF_NEAR) or NEAR_WAKE, int(options.get(CONF_CARD_IDLE) or CARD_IDLE)


# The entities on the cards' device.
PLATFORMS = ["select", "switch", "button"]
# A notification (fledge.notify), and the event its answer fires.
ALERT_TOPIC = "{prefix}/{serial}/set/alert"
EVENT_NOTIFICATION = "fledge_notification"
# A media player's album art (retained; empty for none).
ART_TOPIC = "{prefix}/{serial}/set/art/{entity}"

