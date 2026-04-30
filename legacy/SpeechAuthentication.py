import pyaudio
import wave
import speech_recognition as sr
import os

# Function to train a new user's voice and store it
def train_voice(username):
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 44100
    RECORD_SECONDS = 5
    WAVE_OUTPUT_FILENAME = f"{username}_voice_sample.wav"

    audio = pyaudio.PyAudio()

    # Start recording
    print("Please say something to train your voice.")
    stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
    frames = []
    for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
        data = stream.read(CHUNK)
        frames.append(data)
    print("Voice trained successfully.")

    # Stop recording and save voice sample
    stream.stop_stream()
    stream.close()
    audio.terminate()

    with wave.open(WAVE_OUTPUT_FILENAME, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))

# Function to authenticate the user's voice during login
def authenticate_voice():
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Please say something to authenticate.")
        audio = recognizer.listen(source)

    # Compare the recorded voice with stored voice samples
    for file in os.listdir("."):
        if file.endswith("_voice_sample.wav"):
            with sr.AudioFile(file) as f:
                recorded_audio = recognizer.record(f)
                try:
                    recognized_text = recognizer.recognize_google(recorded_audio)
                    # You can use a more sophisticated voice recognition algorithm here
                    # For simplicity, we're just comparing the recognized text
                    if recognized_text == "hello":  # Assuming the passphrase is "hello"
                        username = file.split("_")[0]
                        print(f"Hello welcome back {username}")
                        return
                except sr.UnknownValueError:
                    pass

    print("Voice authentication failed.")

# Example usage
def main():
    # Train voice for a new user
    train_voice("John")

    # Authenticate voice during login
    authenticate_voice()

if __name__ == "__main__":
    main()
