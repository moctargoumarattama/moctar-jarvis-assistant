import platform
import ctypes
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

def adjust_volume(change):
    if platform.system() == 'Windows':
        # Get the default audio endpoint
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(
            IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        # Get the current volume range
        volume_range = volume.GetVolumeRange()
        min_volume, max_volume, _ = volume_range

        # Get the current volume level
        current_volume = volume.GetMasterVolumeLevelScalar()

        # Calculate the new volume level
        new_volume = min(1.0, max(0.0, current_volume + change / 100.0))

        # Set the new volume level
        volume.SetMasterVolumeLevelScalar(new_volume, None)
        print(f"Adjusted volume by {change} points.")

    else:
        print("Adjusting volume is not supported on this platform.")

def process_command(command):
    if "increase" in command:
        adjust_volume(10)  # Increase volume by 10%
    elif "decrease" in command:
        adjust_volume(-10)  # Decrease volume by 10%
    else:
        print("Command not recognized.")

