import pandas as pd
import pywhatkit
import time
import pyautogui
import text2speech as t2s
import speech2text as s2t
import pyttsx3
import difflib
engine = pyttsx3.init()
def send_message(target_name, message):
    # Read the CSV file into a DataFrame
    csv_file = 'contacts.csv'
    try:
        df = pd.read_csv(csv_file)
        if 'Name' not in df.columns or 'Phone' not in df.columns:
            raise ValueError("CSV file does not contain 'Name' or 'Phone' columns.")
    except Exception as e:
        print("Error:", e)
        return

    # Initialize phone variable
    phone = ""

    # Find the closest match for the target name
    matches = difflib.get_close_matches(target_name, df['Name'], n=1, cutoff=0.5)
    if matches:
        matched_name = matches[0]
        # Get the phone number corresponding to the matched name
        phone = df[df['Name'] == matched_name]['Phone'].iloc[0]

        # Remove any spaces and format the phone number
        phone = ''.join(phone.split())
        phone = phone.replace(" ", "")
        phone = phone.replace(" ", "")

        # Send message
        pywhatkit.sendwhatmsg_instantly(phone, message)
        time.sleep(10)
        t2s.text2speech(engine, "Message sent successfully")

        pyautogui.press('enter')
        print("Message sent to {}: {}".format(matched_name, phone))
    else:
        t2s.text2speech(engine, "Contact " + phone + " not found")
        print("No match found for the given name.")
        
def split_input(input_string):
    # Split the input string into parts
    parts = input_string.split()

    # Initialize variables to store message and contact
    message = ""
    contact = ""

    # Find the position of "send" and "to" in the input string
    send_index = parts.index("send")
    to_index = parts.index("to")

    # Extract message
    message_start_index = send_index + 1
    message_end_index = to_index
    message = " ".join(parts[message_start_index:message_end_index])

    # Extract contact
    contact_start_index = to_index + 1
    contact = " ".join(parts[contact_start_index:])

    return message, contact

def main():
    t2s.text2speech(engine, "Whatsapp initiated")
    while True:
        user_input = (s2t.voice2text("en")).lower()
        print(user_input)
        if user_input:
            if "stop" in user_input or "close youtube" in user_input:
                print("Exiting program...")
                t2s.text2speech(engine, "thank you sir")
                return
                break
            elif "send" in user_input:
                t2s.text2speech(engine, "Message is sent shortly")
                msg,con=split_input(user_input)
                send_message(con, msg)
           

            else:
                return user_input



