from src.control.control_plane import refresh_control_state
from src.observability.status_summary import write_status
refresh_control_state()
print(write_status())
