import sys
from PyQt5.QtWidgets import QWidget, QApplication
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QMovie
from jarvisMainGUI import Ui_JarvisMainGUI
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import pandas as pd
import pyautogui as pg
import spacy
import text2speech as t2s
import speech2text as s2t
import string
import Email
import WeatherUpdates
import utubeVideoDownloader
import gptIntegration
import ScheduleGmeet
import pyttsx3
import searchFile
import winsearch as ws
import web_function as wb
import increaseBrightness as iB
import increaseVolume as iV

class VoiceListener(QThread):
    new_text_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super(VoiceListener, self).__init__(parent)

    def run(self):
        ln = "en"
        while True:
            input_text = s2t.voice2text(ln)
            input_text = input_text.lower()
            if input_text:
                self.new_text_signal.emit(input_text)


class LoginWindow(QWidget):
    def __init__(self):
        super(LoginWindow, self).__init__()
        print("Setting up GUI")
        self.jarvis_ui = Ui_JarvisMainGUI()
        self.jarvis_ui.setupUi(self)
        self.jarvis_ui.pushButton.clicked.connect(self.close)

        # Initialize engine and NLP model
        self.engine = pyttsx3.init()
        self.nlp = spacy.load('en_core_web_sm')

        # Load dataset and vectorizer
        self.df = pd.read_csv('os_dataset.csv')
        self.df.dropna(subset=['text'], inplace=True)
        self.df.reset_index(drop=True, inplace=True)
        self.vectorizer = CountVectorizer().fit(self.df['text'])

        # Load dataset for web
        self.dfw = pd.read_csv('web_Dataset.csv')
        self.dfw.dropna(subset=['text'], inplace=True)
        self.dfw.reset_index(drop=True, inplace=True)
        self.vectorizerw = CountVectorizer().fit(self.dfw['text'])

        t2s.text2speech(self.engine, "Jarvis Activated")
        # Connect voice recognition function to GUI button
        self.jarvis_ui.submitBtn.clicked.connect(self.run_jarvis)

        # Connect text box return pressed signal to execute command
        self.jarvis_ui.terminalInputBox.returnPressed.connect(self.run_jarvis)

        # Start continuous listening in a separate thread
        self.listener_thread = VoiceListener()
        self.listener_thread.new_text_signal.connect(self.execute_command)
        self.listener_thread.start()
        self.runAllMovies()

    def terminalPrint(self, text):
        self.jarvis_ui.terminalOutputBox.appendPlainText(text)

    def runAllMovies(self):
        self.jarvis_ui.listeningMovie = QMovie("D:\BSc CSD Sem 6\Project\JarvisGUI\JarvisImages\\voicerecog.gif")
        self.jarvis_ui.micImg.setMovie(self.jarvis_ui.listeningMovie)
        self.jarvis_ui.listeningMovie.start()

        self.jarvis_ui.arcreactorMovie = QMovie("D:\BSc CSD Sem 6\Project\JarvisGUI\JarvisImages\\arcreactor.gif")
        self.jarvis_ui.arc_reactor.setMovie(self.jarvis_ui.arcreactorMovie)
        self.jarvis_ui.arcreactorMovie.start()

    def preprocess_text(self, text):
        doc = self.nlp(text)
        tokens = [token.text.lower() for token in doc if not token.is_stop and token.text not in string.punctuation]
        processed_text = " ".join(tokens)
        return processed_text

    def predict_label(self, input_text, vectorizer, df):
        preprocessed_input_text = self.preprocess_text(input_text)
        input_vector = vectorizer.transform([preprocessed_input_text])
        similarities = cosine_similarity(input_vector, vectorizer.transform(df['text']))
        max_index = similarities.argmax()
        max_similarity = similarities[0, max_index]
        return df['label'][max_index], max_similarity

    def run_jarvis(self):
        input_text = self.jarvis_ui.terminalInputBox.text().lower()
        ln = "en"
        if input_text:
            self.terminalPrint(f"You: {input_text}")
            self.execute_command(input_text)
            self.jarvis_ui.terminalInputBox.clear()

    def execute_command(self, input_text):
        ln = "en"

        if "send email" in input_text:
            # Email sending logic
            t2s.text2speech(self.engine, "Tell recipient's email address")
            receiver_email = s2t.voice2text("en").lower().replace(" ", "")
            while not receiver_email:
                receiver_email = s2t.voice2text("en").lower().replace(" ", "")
            t2s.text2speech(self.engine, "Subject of the email")
            subject = s2t.voice2text("en")
            while not subject:
                subject = s2t.voice2text("en")
            t2s.text2speech(self.engine, "Body of the email")
            body = s2t.voice2text("en")
            while not body:
                body = s2t.voice2text("en")
            Email.send_email(receiver_email, subject, body)
            self.terminalPrint("Jarvis: Email sent successfully")

        elif "activate type mode" in input_text:
            t2s.text2speech(self.engine, "ok sir i am ready")
            while True:
                st = s2t.voice2text(ln)
                st1 = st.lower()
                if "stop typing" in st1:
                    t2s.text2speech(self.engine, "Typing Stopped")
                    break
                elif "enter" in st1:
                    pg.press('enter')
                elif "backspace" in st1:
                    pg.press('backspace')
                elif "tab" in st1:
                    pg.press('tab')
                else:
                    pg.write(st + " ")

        elif "save" in input_text and "file" in input_text:
            # Extracting the file name from the input text
            file_name_index = input_text.find("as") + 3  # Index after "as" keyword
            file_name = input_text[file_name_index:].strip()  # Extracting the file name
            file_path = file_name + ".txt"  # Appending .txt extension

            # Sending keyboard shortcuts to save the file
            pg.hotkey('ctrl', 's')
            pg.write(file_path)  # Typing the file path
            pg.press('enter')  # Pressing Enter to save
            pg.press('enter')  # Pressing Enter again to confirm if needed

            t2s.text2speech(self.engine, "File saved successfully")

        elif "close app" in input_text or "close current app" in input_text or "close it" in input_text:
            pg.click(300, 300)
            pg.hotkey('alt', 'F4')

        elif "switch next" in input_text:
            pg.click(300, 300)
            pg.hotkey('alt', 'tab')

        elif "switch previous" in input_text:
            pg.click(300, 300)
            pg.hotkey('alt', 'shift', 'tab')

        elif "check weather" in input_text:
            # Weather checking logic
            t2s.text2speech(self.engine, "Which city do you want to check weather for?")
            city_name = s2t.voice2text("en")
            while not city_name:
                t2s.text2speech(self.engine, "Which city do you want to check weather for?")
                city_name = s2t.voice2text("en")
                city_name.strip()
            if city_name.lower() != 'exit':
                weather = WeatherUpdates.get_weather(city_name)
                self.terminalPrint(f"Jarvis: Todays weather in {city_name} is {weather} degree celcius")
        elif "download youtube video" in input_text:
            # YouTube video downloading logic
            t2s.text2speech(self.engine, "Enter the URL of the video:")
            url = s2t.voice2text("en")
            t2s.text2speech(self.engine, "Enter the path to save the video:")
            save_path = s2t.voice2text("en")
            utubeVideoDownloader.download_video(url, save_path)
            self.terminalPrint("Jarvis: The video is saved to your downloads")
        elif "go to interactive mode" in input_text:
            # Interactive mode logic
            gptIntegration.chat()
        elif "schedule meeting" in input_text:
            # Meeting scheduling logic
            ScheduleGmeet.main()
            self.terminalPrint("Jarvis: Yes sir")

        elif "search file" in input_text:
            # File search logic
            t2s.text2speech(self.engine, "What is the name of the file you want to search?")
            name = s2t.voice2text(ln)
            searchFile.open_windows_search(name)
        elif "change to tamil" in input_text:
            ln = "ta"
            self.terminalPrint("Jarvis: Now, you can speak in Tamil")
        elif "change to english" in input_text:
            ln = "en"
            self.terminalPrint("Jarvis: Now, you can speak in English")
        elif "switch to jarvis" in input_text:
            # Switch to Jarvis voice
            t2s.switch_voice(self.engine, 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_DAVID_11.0')
            self.terminalPrint("Jarvis: Voice changed to Male")
        elif "switch to friday" in input_text:
            # Switch to Friday voice
            t2s.switch_voice(self.engine, 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_ZIRA_11.0')
            self.terminalPrint("Jarvis: Voice changed to female")
        elif "increase brightness" in input_text:
            iB.increase_brightness()
            t2s.text2speech(self.engine, "Brightness increased")
        elif "decrease brightness" in input_text:
            iB.decrease_brightness()
            t2s.text2speech(self.engine, "Brightness decreased")
        elif "increase volume" in input_text:
            iV.process_command("increase")
            t2s.text2speech(self.engine, "Volume increased")
        elif "decrease volume" in input_text:
            iV.process_command("decrease")
            t2s.text2speech(self.engine, "Volume decreased")
        else:
            predicted_label, max_similarity = self.predict_label(input_text, self.vectorizer, self.df)
            print("Predicted label:", predicted_label)
            print("Maximum similarity score:", max_similarity)

            predicted_labelw, max_similarityw = self.predict_label(input_text, self.vectorizerw, self.dfw)
            print("Predicted label:", predicted_labelw)
            print("Maximum similarity score:", max_similarityw)
            if predicted_label == "open powerpoint" and "powerpoint" in input_text:
                max_similarity += 0.1
            elif predicted_label == "open powerpoint":
                max_similarity -= 0.1
            if predicted_label == "open firefox" and "firefox" in input_text:
                max_similarityw += 0.1
            elif predicted_label == "open firefox":
                max_similarityw -= 0.5

            if max_similarity < max_similarityw:
                self.listener_thread._is_running = False
                if "open" in predicted_labelw:
                    app = predicted_labelw.replace("open ", "")
                    ip = wb.web_open(app, input_text)
                    print(app)
                    t2s.text2speech(self.engine, "Yes sir")
                    self.listener_thread.start()
                elif "close" in predicted_labelw:
                    app = predicted_labelw.replace("close ", "")
                    pg.click(300, 300)
                    pg.hotkey('ctrl', 'w')
                    t2s.text2speech(self.engine, "Yes sir")
            else:
                if "open" in predicted_label:
                    app = predicted_label.replace("open ", "")
                    print("App: " + app)
                    ws.os_open(app)

                elif "close" in predicted_label:
                    app = predicted_label.replace("close ", "")
                    ws.close_application(app)
                    t2s.text2speech(self.engine, "Yes sir")
                elif "date" in predicted_label:
                    app = predicted_label.replace("date ", "")
                    ws.os_open(app)
                elif "lock the screen" in predicted_label:
                    # Lock the screen logic
                    pg.hotkey('win', 'l')
                    self.terminalPrint("Jarvis: Screen locked")
                    t2s.text2speech(self.engine, "Screen locked")
                elif "go to sleep" in predicted_label or "screen off" in predicted_label:
                    # Go to sleep logic
                    pg.hotkey('win', 'x')
                    pg.hotkey('u', 's')
                    # Add your code to put the system to sleep here
                    self.terminalPrint("Jarvis: Going to sleep")
                    t2s.text2speech(self.engine, "Going to sleep")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ui = LoginWindow()
    ui.show()
    sys.exit(app.exec_())
