import subprocess

def list_installed_apps_windows():
    result = subprocess.run(['powershell', 'Get-AppxPackage'], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.split('\n')
    else:
        return []

# Example usage
installed_apps = list_installed_apps_windows()
file = open("App list.txt","w")
for app in installed_apps:
    file.write(app)
    file.write("\n")
    print(app)
