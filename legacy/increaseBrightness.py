import platform

def increase_brightness():
    if platform.system() == 'Windows':
        # Adjust screen brightness on Windows
        try:
            import wmi

            brightness = wmi.WMI(namespace='wmi')
            methods = brightness.WmiMonitorBrightnessMethods()[0]
            methods.WmiSetBrightness(80, 0)  # Change 80 to the desired brightness level (0-100)
            print("Brightness increased.")
        except Exception as e:
            print("Failed to increase brightness:", e)

    elif platform.system() == 'Darwin':
        # Adjust screen brightness on macOS
        try:
            import screenbrightness

            current_brightness = screenbrightness.get_brightness()
            new_brightness = min(1.0, max(0.0, current_brightness + 0.1))  # Increase brightness by 0.1
            screenbrightness.set_brightness(new_brightness)
            print("Brightness increased.")
        except Exception as e:
            print("Failed to increase brightness:", e)

    else:
        print("Adjusting brightness is not supported on this platform.")

def decrease_brightness():
    if platform.system() == 'Windows':
        # Adjust screen brightness on Windows
        try:
            import wmi

            brightness = wmi.WMI(namespace='wmi')
            methods = brightness.WmiMonitorBrightnessMethods()[0]
            methods.WmiSetBrightness(20, 0)  # Change 20 to the desired brightness level (0-100)
            print("Brightness decreased.")
        except Exception as e:
            print("Failed to decrease brightness:", e)

    elif platform.system() == 'Darwin':
        # Adjust screen brightness on macOS
        try:
            import screenbrightness

            current_brightness = screenbrightness.get_brightness()
            new_brightness = min(1.0, max(0.0, current_brightness - 0.1))  # Decrease brightness by 0.1
            screenbrightness.set_brightness(new_brightness)
            print("Brightness decreased.")
        except Exception as e:
            print("Failed to decrease brightness:", e)

    else:
        print("Adjusting brightness is not supported on this platform.")

# Test the functions
# command = input("Enter your command: ").lower()


