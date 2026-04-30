import subprocess

def run_selenium_side(file_path):
    # Path to the selenium-side-runner executable
    runner_path = "/path/to/selenium-side-runner"

    # Command to run the .side file
    command = [runner_path, file_path]

    # Execute the command
    subprocess.run(command)

if __name__ == "__main__":
    # Specify the path to your .side file
    side_file_path = "/path/to/your/file.side"

    # Call the function to run the .side file
    run_selenium_side(side_file_path)
