"""Constants for CarSentry."""

DOMAIN = "carsentry"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_BASE_URL = "base_url"

DEFAULT_PORT = 8010
DEFAULT_SCAN_INTERVAL = 10  # seconds

# Supervisor slug of the addon
ADDON_SLUG = "local_carsentry"
# Fallback internal hostname when running as HA addon
ADDON_HOSTNAME = "carsentry"
