import pyttsx3
import speech_recognition as sr
import csv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
import subprocess

recognizer = sr.Recognizer()

# Initialize the pyttsx3 engine
engine = pyttsx3.init()

# Function to speak text
def speak(text):
    engine.say(text)
    engine.runAndWait()

# Load data from CSV
sentences = []
commands = []

with open('os_dataset.csv', 'r', newline='') as file:
    csv_reader = csv.reader(file)
    next(csv_reader)  # Skip header
    for row in csv_reader:
        sentences.append(row[0])
        commands.append(row[1])

# Feature extraction
vectorizer = TfidfVectorizer(stop_words='english')
X = vectorizer.fit_transform(sentences)

# Training the model
model = LinearSVC()
model.fit(X, commands)

# New sentence to predict
while True:
    text = ""
    with sr.Microphone() as source:
        print("Speak something...")
        # Adjust for ambient noise if needed
        recognizer.adjust_for_ambient_noise(source)

        # Listen for the audio input
        audio = recognizer.listen(source)

        try:
            print("Transcribing...")
            text = recognizer.recognize_google(audio)
            print("You said:", text)
            speak(text)  # Speak back the user's input

        except sr.UnknownValueError:
            print("Sorry, could not understand audio.")
        except sr.RequestError as e:
            print("Error fetching results; {0}".format(e))

    # Transform the new sentence using the same vectorizer
    new_sentence_vectorized = vectorizer.transform([text])

    # Predict the corresponding commandExactly exactly
    predicted_command = model.predict(new_sentence_vectorized)
    print("Predicted command:", predicted_command)
    cmd = predicted_command[0]
    cmd = cmd.replace("open_", "")
    command = "start " + cmd
    print("Command: ", command)
    # subprocess.run(command, shell=True)
