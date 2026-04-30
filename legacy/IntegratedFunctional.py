import Email
import WeatherUpdates
import speech2text
import utubeVideoDownloader
import gptIntegration
import ScheduleGmeet

print("\nStarted listening... Say 'exit' to stop")

def main():
    while True:
        user_input = speechRecog.voice()
        user_input=user_input.lower()
        if not user_input:
            continue

        if "send email" in user_input:
            print("Recipient's Email:")
            receiver_email = speechRecog.voice().lower().replace(" ", "")
            while not receiver_email:
                receiver_email = speechRecog.voice().lower().replace(" ", "")
            print("Receiver:", receiver_email)
            print("Subject:")
            subject = speechRecog.voice()
            while not subject:
                subject = speechRecog.voice()
            print("Body:")
            body = speechRecog.voice()
            while not body:
                body = speechRecog.voice()
            Email.send_email(receiver_email, subject, body)

        elif "check weather" in user_input:
            city_name = speechRecog.voice()
            while not city_name:
                print("Which city do you want to check weather for?")
                city_name = speechRecog.voice()
                city_name.strip()
            if city_name.lower() == 'exit':
                break
            else:
                WeatherUpdates.get_weather(city_name)

        elif "download youtube video" in user_input:
            url = input("Enter the URL of the video: ")
            save_path = input("Enter the path to save the video: ")
            # save_path="Videos"
            utubeVideoDownloader.download_video(url, save_path)

        elif "go to interactive mode" in user_input:
            gptIntegration.chat()

        elif "schedule a meet" in user_input:
            ScheduleGmeet.main()

        elif "exit" in user_input:
            break

        else:
            print("Invalid command. Please try again.")

if __name__ == "__main__":
    main()

